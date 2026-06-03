import sqlite3
import cv2 as cv
import csv
import os
import time
from datetime import datetime

CSV_FILE = "gap_measurements.csv"
DB_FILE = "gap_measurements.db"
IMAGE_DIR = "captured_images"

last_image_save_time = 0

def init_log():
    if not os.path.exists(IMAGE_DIR): os.makedirs(IMAGE_DIR)
    if not os.path.exists(CSV_FILE):
        with open(CSV_FILE, 'w', newline='') as f:
            csv.writer(f).writerow(["Timestamp", "Date", "Distance", "k", "Ax", "Ay", "Az", "Xx", "Xy", "Xz", "TRx", "TRy", "TRz", "BRx", "BRy", "BRz", "Bx", "By", "Bz", "Cx", "Cy", "Cz", "Ux", "Uy", "Uz", "Vx", "Vy", "Vz", "Wx", "Wy", "Wz", "L_Roll", "L_Tilt", "R_Roll", "R_Tilt"])
    conn = sqlite3.connect(DB_FILE)
    conn.execute('''CREATE TABLE IF NOT EXISTS measurements (id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp REAL, date_str TEXT, distance REAL, k REAL, Ax REAL, Ay REAL, Az REAL, Xx REAL, Xy REAL, Xz REAL, TRx REAL, TRy REAL, TRz REAL, BRx REAL, BRy REAL, BRz REAL, Bx REAL, By REAL, Bz REAL, Cx REAL, Cy REAL, Cz REAL, Ux REAL, Uy REAL, Uz REAL, Vx REAL, Vy REAL, Vz REAL, Wx REAL, Wy REAL, Wz REAL, L_Roll REAL, L_Tilt REAL, R_Roll REAL, R_Tilt REAL)''')
    conn.commit()
    conn.close()

def save_image(frame):
    global last_image_save_time
    if (time.time() - last_image_save_time) >= 0.5:
        ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S-%f")[:-3]
        cv.imwrite(os.path.join(IMAGE_DIR, f"cap_{ts}.jpg"), frame)
        last_image_save_time = time.time()

# MUST HAVE 13 ARGUMENTS:
def record(dist, k, a, x, tr, br, b, c, u, v, w, l_rot, r_rot):
    """Logs data including marker rotation angles."""
    curr_time = round(time.time(), 3)
    date_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    rv = lambda vec: [round(float(val), 3) for val in vec]
    
    clean_data = [curr_time, date_str, round(dist, 3), round(k, 3), 
                  *rv(a), *rv(x), *rv(tr), *rv(br), *rv(b), *rv(c), *rv(u), *rv(v), *rv(w),
                  round(l_rot[0], 3), round(l_rot[1], 3), round(r_rot[0], 3), round(r_rot[1], 3)]

    with open(CSV_FILE, 'a', newline='') as f:
        csv.writer(f).writerow(clean_data)

    conn = sqlite3.connect(DB_FILE)
    sql = 'INSERT INTO measurements (timestamp, date_str, distance, k, Ax, Ay, Az, Xx, Xy, Xz, TRx, TRy, TRz, BRx, BRy, BRz, Bx, By, Bz, Cx, Cy, Cz, Ux, Uy, Uz, Vx, Vy, Vz, Wx, Wy, Wz, L_Roll, L_Tilt, R_Roll, R_Tilt) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)'
    conn.execute(sql, clean_data)
    conn.commit()
    conn.close()
