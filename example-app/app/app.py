import argparse
import logging
import os
import threading
import time
import cv2
import ffmpeg
import numpy as np
import subprocess as sp
import pytesseract


from openvino.inference_engine import IECore
from ffmpeg import Error as FFmpegError
from paho.mqtt import client as paho_mqtt_client

logging.basicConfig(format='[%(asctime)s] [%(levelname)8s] --- %(message)s', level=logging.INFO)
ROOT = os.path.dirname(__file__)


########################################################################################################################
# UTILS
########################################################################################################################
def draw_text(img, text, font=cv2.FONT_HERSHEY_PLAIN, pos=(0, 0), font_scale=2, font_thickness=1,
              text_color=(0, 0, 255), text_color_bg=(0, 0, 0)):
    x, y = pos
    text_size, _ = cv2.getTextSize(text, font, font_scale, font_thickness)
    text_w, text_h = text_size
    cv2.rectangle(img, pos, (x + text_w, y + text_h), text_color_bg, -1)
    cv2.putText(img, text, (x, y + text_h + font_scale - 1), font, font_scale, text_color, font_thickness)

    return text_size


def multiply_by_ratio(ratio_x, ratio_y, box):
    return [
        max(shape * ratio_y, 10) if idx % 2 else shape * ratio_x
        for idx, shape in enumerate(box[:-1])
    ]


########################################################################################################################
# MQTT CALLBACKS
########################################################################################################################
def on_connect(client, userdata, flags, rc):
    if rc == 0:
        logging.info("Connected to MQTT Broker!")
    else:
        logging.info(f"Failed to connect to MQTT Broker, return code {rc}")


########################################################################################################################
# TEST INPUT RTSP STREAM
########################################################################################################################
def probe_rtsp_stream(input_rtsp_url):
    # Get width and height from input stream and test connectivity
    width = None
    height = None
    try:
        probe = ffmpeg.probe(input_rtsp_url, rtsp_transport='tcp', stimeout='5000000')
        video_stream = next((stream for stream in probe['streams'] if stream['codec_type'] == 'video'), None)
        try:
            width = int(video_stream['width'])
            height = int(video_stream['height'])
        except Exception:
            width = None
            height = None

    except FFmpegError as e:
        logging.info(e.stderr.decode('utf8'))

    return width, height


########################################################################################################################
# READ INPUT RTSP STREAM
########################################################################################################################
def receive(input_rtsp_url):
    global RAW_FRAME, LOCK_RAW_FRAME

    # FFMPEG command
    ffmpeg_com = (ffmpeg
                  .input(input_rtsp_url, fflags='nobuffer', flags='low_delay', rtsp_transport='tcp', stimeout='1000000')
                  .output('pipe:', format='rawvideo', pix_fmt='bgr24')
                  .compile())

    width = None
    height = None
    frame_size = None
    process_ffmpeg = None
    while True:
        if process_ffmpeg is None:
            while width is None or height is None:
                width, height = probe_rtsp_stream(input_rtsp_url=input_rtsp_url)
            frame_size = width * height * 3
            process_ffmpeg = sp.Popen(ffmpeg_com, stdout=sp.PIPE)

        in_bytes = process_ffmpeg.stdout.read(frame_size)

        if len(in_bytes) == 0:
            # Something bad happens with Input RTSP, kill actual ffmpeg process and clean variables
            frame = None
            with LOCK_RAW_FRAME:
                RAW_FRAME = frame

            # kill ffmpeg process
            process_ffmpeg.kill()
            # clean variables
            width = None
            height = None
            frame_size = None
            process_ffmpeg = None
        else:
            frame = (np.frombuffer(in_bytes, np.uint8).reshape([height, width, 3]))
            with LOCK_RAW_FRAME:
                RAW_FRAME = frame


def text_detection(frame, model_b, model_h, model_w, model_input_layer, model_output_layer,
                   model_network_loaded_on_device, confidence):
    # Resize frame to network for model 1
    resized_image = cv2.resize(frame, (model_w, model_h), interpolation=cv2.INTER_NEAREST)

    # RGB TO BGR
    resized_image = cv2.cvtColor(resized_image, cv2.COLOR_RGB2BGR)

    # (H,W,C) -> (C,H,W)
    input_frame = resized_image.transpose((2, 0, 1))

    # Set batch
    batched_frame = np.array([input_frame for _ in range(model_b)])

    # Start an inference of the loaded network and return output data
    result = model_network_loaded_on_device.infer(inputs={model_input_layer: batched_frame})

    list_detected_text_bbox = []
    if "boxes" in result:
        # Extract list of boxes from results
        boxes = result["boxes"]

        # Remove zero only boxes
        boxes = boxes[~np.all(boxes == 0, axis=1)]

        # Iterate through non-zero boxes
        for box in boxes:
            # Pick confidence factor from last place in array
            conf = box[4]

            if conf > confidence:
                # Fetch image shapes to calculate ratio
                (real_y, real_x), (resized_y, resized_x) = frame.shape[:2], resized_image.shape[:2]
                ratio_x, ratio_y = real_x / resized_x, real_y / resized_y

                # Convert float to int and multiply position of each box by x and y ratio
                (xmin, ymin, xmax, ymax) = map(int, multiply_by_ratio(ratio_x, ratio_y, box))

                text_bbox = (xmin, ymin, xmax, ymax)
                list_detected_text_bbox.append(text_bbox)

    return list_detected_text_bbox


def text_recognition(text_frame):
    # DO BLACK MAGIC

    # IF WE DETECT TEXT, RETURN RECOGNIZED TEXT AS STRING
    # IF WE DONT DETECT TEXT, RETURN NONE

    recognized = True
    if recognized:
        img1 = np.array(text_frame)
        text = pytesseract.image_to_string(img1)

        text1 = ''.join(ch for ch in text if ch.isalnum())
        return text1
    else:
        return None


def openvino_inference(output_rtsp_url, conf_text_detection, ip_mqtt_broker, port_mqtt_broker, username_mqtt_broker,
                       password_mqtt_broker):
    """
    Process frames using OpenVino Inference Engine
    """
    global RAW_FRAME, LOCK_RAW_FRAME

    ####################################################################################################################
    # Publish RTSP stream into a RTSP Server
    ####################################################################################################################
    command = ['ffmpeg',
               '-re',
               '-i', '-',
               '-g', '50',
               '-c:v', 'libx264',
               '-preset', 'ultrafast',
               '-tune', 'zerolatency',
               '-pix_fmt', 'yuv420p',
               '-rtsp_transport', 'tcp',
               '-f', 'rtsp',
               output_rtsp_url]
    process_ffmpeg = sp.Popen(command, stdin=sp.PIPE)

    ####################################################################################################################
    # Connect to MQTT Broker
    ####################################################################################################################
    mqtt_client = paho_mqtt_client.Client()
    mqtt_client.username_pw_set(username=username_mqtt_broker,
                                password=password_mqtt_broker)
    mqtt_client.on_connect = on_connect  # attach function to callback
    mqtt_client.connect(host=str(ip_mqtt_broker),
                        port=int(port_mqtt_broker))
    mqtt_client.loop_start()

    ####################################################################################################################
    # OpenVINO Inference Engine Core
    ####################################################################################################################

    # Create an instance of the OpenVINO Inference Engine Core
    ie_core = IECore()

    # Read models in OpenVINO Intermediate Representation (.xml and .bin files)
    # Model 1: Vehicle detection
    model1_network = ie_core.read_network('openvino_models/FP16/horizontal-text-detection-0001.xml',
                                          'openvino_models/FP16/horizontal-text-detection-0001.bin')

    # Get input and output layers of the networks
    model1_input_layer = next(iter(model1_network.inputs))
    model1_output_layer = next(iter(model1_network.outputs))

    # Get shape network (batch, channels, height, width)
    model1_b, model1_c, model1_h, model1_w = model1_network.inputs[model1_input_layer].shape

    # Load the network that was read from the Intermediate Representation (IR) to device
    model1_network_loaded_on_device = ie_core.load_network(network=model1_network, device_name="CPU")

    while True:
        with LOCK_RAW_FRAME:
            frame = RAW_FRAME

        if frame is None:
            frame = np.zeros((576, 1024, 3), np.uint8)
            font = cv2.FONT_HERSHEY_SIMPLEX
            text = "NO VIDEO"

            # get boundary of this text
            textsize = cv2.getTextSize(text, font, 1, 2)[0]
            textX = int((frame.shape[1] - textsize[0]) / 2)
            textY = int((frame.shape[0] + textsize[1]) / 2)

            cv2.putText(frame, text, (textX, textY), font, 1, (255, 255, 255), 2)
        else:
            inference_start_time = time.time()

            ############################################################################################################
            # 1. Text detection using OpenVINO
            ############################################################################################################
            list_detected_text_bbox = text_detection(frame=frame,
                                                     model_b=model1_b,
                                                     model_h=model1_h,
                                                     model_w=model1_w,
                                                     model_input_layer=model1_input_layer,
                                                     model_output_layer=model1_output_layer,
                                                     model_network_loaded_on_device=model1_network_loaded_on_device,
                                                     confidence=conf_text_detection)

            # ------> Hackathon starts here <---------
            for detected_text_bbox in list_detected_text_bbox:
            
                xmin = detected_text_bbox[0] - 10
                ymin = detected_text_bbox[1] - 10
                xmax = detected_text_bbox[2] + 10
                ymax = detected_text_bbox[3] + 10

                
              
                
                try:
                    text_frame = frame[ymin:ymax, xmin:xmax]
                except:
                    text_frame = None

                if text is not None:
                    # ------> Hackathon text recognition <---------
                    text = text_recognition(text_frame)
                    # ------> Hackathon text recognition <---------
                    # Draw a rectangle over each detected text
                    cv2.rectangle(frame, (xmin, ymin), (xmax, ymax), (0, 255, 0), 2)

                    # Print text over draw rectangle

                    
                    
                    text_size, _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_PLAIN, 2, 1)
                    text_w, text_h = text_size
                    draw_text(frame,
                              text,
                              font_scale=2,
                              font_thickness=1,
                              pos=(xmin, ymin - text_h),
                              text_color_bg=(0, 255, 0), text_color=(0, 0, 0))

                    # Publish recognized text to MQTT Broker
                    mqtt_client.publish(f"mqtt/hackathon_5g/{TEAM_NAME}/text", text)
            # ------> Hackathon ends here <---------

            inference_time = time.time() - inference_start_time
            inference_engine_fps = 1 / inference_time
            logging.info(f"[Text detection] FPS from OpenVINO model: {inference_engine_fps}")

        # Send frame to RTSP Server
        ret, frame = cv2.imencode('.jpg', frame)
        process_ffmpeg.stdin.write(frame)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Hackathon 5G Example APP")
    parser.add_argument("--team_name",
                        type=str,
                        help="Team name.",
                        required=True)
    parser.add_argument("--input_rtsp_url",
                        type=str,
                        help="RTSP URL for input streaming.",
                        required=True)
    parser.add_argument("--output_rtsp_url",
                        type=str,
                        help="RTSP URL for output streaming. This RTSP URL will be injected into RTSP Server",
                        required=True)
    parser.add_argument("--conf_text_detection",
                        type=float,
                        choices=[0., 0.05, 0.1, 0.15, 0.2, 0.25, 0.3, 0.35, 0.4, 0.45, 0.5,
                                 0.55, 0.6, 0.65, 0.7, 0.75, 0.8, 0.85, 0.9, 0.95, 1],
                        default=0.8,
                        help="Confidence between 0 and 1, with 0.05 increment, for the AI model that detects text.")
    parser.add_argument("--ip_mqtt_broker",
                        type=str,
                        help="IP to connect to MQTT Broker",
                        required=True)
    parser.add_argument("--port_mqtt_broker",
                        type=int,
                        help="Port to connect to MQTT Broker",
                        required=True)
    parser.add_argument("--username_mqtt_broker",
                        type=str,
                        help="Username to connect to MQTT Broker",
                        required=True)
    parser.add_argument("--password_mqtt_broker",
                        type=str,
                        help="Password to connect to MQTT Broker",
                        required=True)
    args = parser.parse_args()

    ####################################################################################################################
    # Print input parameters
    ####################################################################################################################
    logging.info('#######################################################################')
    logging.info('# APP PARAMETERS:')
    logging.info('#######################################################################')
    logging.info('\t- TEAM NAME: ' + str(args.team_name))
    logging.info('\t- INPUT RTSP URL: ' + str(args.input_rtsp_url))
    logging.info('\t- OUTPUT RTSP URL: ' + str(args.output_rtsp_url))
    logging.info('\t- CONFIDENCE TEXT DETECTED MODEL: ' + str(args.conf_text_detection))
    logging.info('#######################################################################')
    logging.info('# MQTT PARAMETERS:')
    logging.info('#######################################################################')
    logging.info('\t- IP MQTT BROKER: ' + str(args.ip_mqtt_broker))
    logging.info('\t- PORT MQTT BROKER: ' + str(args.port_mqtt_broker))
    logging.info('\t- USERNAME MQTT BROKER: ' + str(args.username_mqtt_broker))
    logging.info('\t- PASSWORD MQTT BROKER: ' + str(args.password_mqtt_broker))
    logging.info('#######################################################################')

    ####################################################################################################################
    # Global variables
    ####################################################################################################################
    RAW_FRAME = None
    LOCK_RAW_FRAME = threading.Lock()
    TEAM_NAME = args.team_name.lower()

    ####################################################################################################################
    # Start thread for receive input stream
    ####################################################################################################################
    t_receive_stream = threading.Thread(target=receive, kwargs={'input_rtsp_url': args.input_rtsp_url})
    t_receive_stream.start()

    ####################################################################################################################
    # Start thread for openvino inference for text detection and recognition
    ####################################################################################################################
    t_openvino_inference = threading.Thread(target=openvino_inference,
                                            kwargs={'output_rtsp_url': args.output_rtsp_url,
                                                    'conf_text_detection': args.conf_text_detection,
                                                    'ip_mqtt_broker': args.ip_mqtt_broker,
                                                    'port_mqtt_broker': args.port_mqtt_broker,
                                                    'username_mqtt_broker': args.username_mqtt_broker,
                                                    'password_mqtt_broker': args.password_mqtt_broker})
    t_openvino_inference.start()