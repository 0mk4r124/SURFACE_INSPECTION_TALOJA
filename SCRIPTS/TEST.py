# import os
# import traceback
# import pymssql

# # from DEPENDANT.LOGIX import PLCCOMMINCATION
# from DEPENDANT.LOGGING import initializeLogger
# from DEPENDANT.MQTT import MQTT


# BASE_FILE_PATH = os.environ.get('BASE_FILE_PATH', '/home/hindalco_surface_inspection/insightzz/SURFACE_INSPECTION_TALOJA/')
# LOGS_PATH = BASE_FILE_PATH + "DEBUGS/LOGS/"

# # Initialize logger
# logger = initializeLogger("TEST", LOGS_PATH=LOGS_PATH)

# FRAME_CAPTURE_SIGNAL = "DataToDeltaPLC"

# sql_server_params = {
#     "server": "192.168.1.33",
#     "port": 1433,
#     "user": "saread",
#     "password": "saread",
#     "database": "L2_Davy"
# }

# # =========================
# # CONNECT TO SQL SERVER
# # =========================

# def dbconn():
#     conn = None
#     try:
#         conn = pymssql.connect(
#             server=sql_server_params["server"],
#             user=sql_server_params["user"],
#             password=sql_server_params["password"],
#             database=sql_server_params["database"],
#             port=sql_server_params["port"],
#             timeout=5
#         )

#         print("Connected successfully")

#     except Exception as e:
#         print("Connection failed :", e)
    
#     return conn

# def main():
#     # Create cursor
#     conn = dbconn()
#     cursor = conn.cursor()

#     # # =========================
#     # # FETCH DATA
#     # # =========================

#     query = "SELECT TOP 10 * FROM VW_LIVE_COIL_DATA"
#     cursor.execute(query)

#     # # Get column names
#     columns = [desc[0] for desc in cursor.description]

#     print("\nColumns:\n", columns)
#     print("\nData:\n")

#     # # Print header
#     print("\t".join(columns))

#     # # Print rows
#     # for row in cursor.fetchall():
#     #     print(
#     #         "\t".join(
#     #             [str(item) if item is not None else "" for item in row]
#     #         )
#     #     )

#     # # Close connection
#     cursor.close()
#     conn.close()

#     mq_obj = MQTT("TEST")
#     # diameter, mill_speed, eg_setpoint, width, length = 0, 0, 0, 150, 0

#     # while True:
#     #     mq_obj.publish("taloja/capture", {"action": "start", "roll_num": "DEMO"})
#     #     while True:
#     #         mq_obj.publish("taloja/capture", {"diameter": diameter, "mill_speed": mill_speed, "eg_setpoint": eg_setpoint, "width": width, "length": length}) 

#     #         diameter += 1
#     #         mill_speed += 10
#     #         eg_setpoint += 0.05
#     #         length += 100

#     #         if length>10000000: break
            

#     #     mq_obj.publish("taloja/capture", {"action": "stop", "roll_num": "DEMO"})



#     # plcobj = PLCCOMMINCATION("192.168.1.13")
#     # plcobj.init()

#     # while True:
#     #     try: 
#     #         result = plcobj.read(f"{FRAME_CAPTURE_SIGNAL}[0]")
#     #         print("[0] :", result)
#     #     except: pass

#     #     try: 
#     #         result = plcobj.read(f"{FRAME_CAPTURE_SIGNAL}[1]")
#     #         print("[1] :", result)
#     #     except: pass

#     #     try: 
#     #         result = plcobj.read(f"{FRAME_CAPTURE_SIGNAL}[2]")
#     #         print("[2] :", result)
#     #     except: pass

#     #     try: 
#     #         result = plcobj.read(f"{FRAME_CAPTURE_SIGNAL}[3]")
#     #         print("[3] :", result)
#     #     except: pass

#     #     try: 
#     #         result = plcobj.read(f"{FRAME_CAPTURE_SIGNAL}[4]")
#     #         print("[4] :", result)
#     #     except: pass

#     #     try: 
#     #         result = plcobj.read(f"{FRAME_CAPTURE_SIGNAL}[5]")
#     #         print("[5] :", result)
#     #     except: pass

#     #     try: 
#     #         result = plcobj.read(f"{FRAME_CAPTURE_SIGNAL}[6]")
#     #         print("[6] :", result)
#     #     except: pass

# if __name__ == "__main__":

#      while True:
#           try: main()
#           except Exception as e:
#                logger.critical(f"Main Outer Loop Exception is {e}")
#                logger.critical(traceback.format_exc())



from pathlib import Path
import argparse
import cv2

def compress_images(input_folder: str, output_folder: str, percentages=(80, 75, 60, 50, 30, 20, 10)):
    input_root = Path(input_folder)
    output_root = Path(output_folder)

    if not input_root.exists():
        raise FileNotFoundError(f"Input folder not found: {input_root}")

    output_root.mkdir(parents=True, exist_ok=True)

    image_extensions = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".webp"}

    uid_dirs = [d for d in input_root.iterdir() if d.is_dir()]
    if not uid_dirs:
        raise FileNotFoundError(f"No UID directories found in: {input_root}")

    for uid_dir in sorted(uid_dirs):
        uid = uid_dir.name
        cam_dirs = [d for d in uid_dir.iterdir() if d.is_dir()]
        if not cam_dirs:
            continue

        print(f"Processing UID: {uid}")
        print(f"From : {len(uid_dirs)} dirs")

        for cam_dir in sorted(cam_dirs):
            cam_name = cam_dir.name
            image_files = [p for p in cam_dir.rglob("*") if p.is_file() and p.suffix.lower() in image_extensions]
            if not image_files:
                continue

            for percent in percentages:
                target_base = output_root / "compressed" / f"{uid}_{percent}" / cam_name
                count = 0
                for image_path in image_files:
                    img = cv2.imread(str(image_path), cv2.IMREAD_COLOR)
                    if img is None:
                        continue

                    suffix = image_path.suffix.lower()
                    if suffix in {".jpg", ".jpeg"}:
                        encode_param = [int(cv2.IMWRITE_JPEG_QUALITY), percent]
                        output_ext = ".jpg"
                    else:
                        png_level = min(9, max(0, int(round((100 - percent) / 10))))
                        encode_param = [int(cv2.IMWRITE_PNG_COMPRESSION), png_level]
                        output_ext = ".png"

                    rel_path = image_path.relative_to(cam_dir).with_suffix(output_ext)
                    output_file = target_base / rel_path
                    output_file.parent.mkdir(parents=True, exist_ok=True)

                    success, encoded = cv2.imencode(output_ext, img, encode_param)
                    if success:
                        with open(output_file, "wb") as f:
                            f.write(encoded.tobytes())

                    count += 1
                    print(f"Done : {(count/len(image_files))*100:.2f}%", end="\r")

    print(f"Compression complete. Compressed folders saved under: {output_root / 'compressed'}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Compress images from GIVEN_DIR/UID/CAMNAME into compressed/UID_PERCENT/CAMNAME using OpenCV"
    )
    parser.add_argument("input_folder", help="Root input folder containing UID subdirectories")
    parser.add_argument("-o", "--output_folder", default=".", help="Root output folder where compressed/ subfolders are created")
    parser.add_argument(
        "-q",
        "--qualities",
        nargs="*",
        type=int,
        default=[60],
        help="Compression quality percentages for JPEG output",
    )
    args = parser.parse_args()

    compress_images(args.input_folder, args.output_folder, tuple(args.qualities))

