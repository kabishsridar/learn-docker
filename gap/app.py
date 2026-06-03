import tkinter as tk
from tkinter import ttk
import cv2 as cv
import numpy as np
import threading
import math
import os
import time
from PIL import Image, ImageTk

import log  # Uses your existing log.py module

# --- DEFAULT SETTINGS ---
DEFAULT_MARKER_SIZE = 53.8             
ARUCO_DICT = cv.aruco.DICT_4X4_50  
RESOLUTION = (1280, 720)          
CALIB_FILE = "camera_params.npz"

class MeasurementApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Multi-Configuration Dual-Pair Dashboard v7.1")
        self.root.geometry("1500x950")
        self.root.configure(bg="#2c3e50")

        # --- DYNAMIC SETTINGS ---
        self.size_top = tk.DoubleVar(value=DEFAULT_MARKER_SIZE)
        self.size_bot = tk.DoubleVar(value=DEFAULT_MARKER_SIZE)

        # --- DATA STORAGE ---
        template = {"A":(0,0,0), "X":(0,0,0), "TR":(0,0,0), "BR":(0,0,0), "B":(0,0,0), "C":(0,0,0), "dist":0.0, "k":0.0,
                    "U":(0,0,0), "V":(0,0,0), "W":(0,0,0), "L_Ang":(0,0), "R_Ang":(0,0)}
        
        self.last_data = {"top": template.copy(), "bottom": template.copy(), "session_count": 0}
        self.is_running = True
        self.current_frame = None

        # --- UI STYLING ---
        style = ttk.Style()
        style.theme_use('clam')
        style.configure("TNotebook", background="#2c3e50")
        style.configure("TFrame", background="#ecf0f1")
        
        self.setup_ui()
        
        # --- START BACKGROUND THREAD ---
        self.thread = threading.Thread(target=self.measurement_loop, daemon=True)
        self.thread.start()

        self.update_gui_loop()

    def setup_ui(self):
        self.tabs = ttk.Notebook(self.root)
        self.tabs.pack(fill='both', expand=True, padx=10, pady=10)

        # TAB 1: LIVE FEED & DUAL STATS
        self.tab_live = ttk.Frame(self.tabs)
        self.tabs.add(self.tab_live, text=" 📽 Live Monitor ")
        
        self.canvas = tk.Canvas(self.tab_live, width=960, height=540, bg="black")
        self.canvas.pack(side="left", padx=20, pady=20)

        stats_container = tk.Frame(self.tab_live, bg="#ecf0f1")
        stats_container.pack(side="right", fill="both", expand=True, padx=10, pady=20)

        # Pair Panels
        for key, title, color in [("top", "TOP PAIR", "#e67e22"), ("bottom", "BOTTOM PAIR", "#9b59b6")]:
            f = tk.LabelFrame(stats_container, text=f" {title} ", font=("Helvetica", 12, "bold"), bg="#ecf0f1", fg=color)
            f.pack(fill="x", pady=10, padx=10)
            dist_lbl = tk.Label(f, text="0.000 mm", font=("Helvetica", 24, "bold"), fg="#27ae60", bg="#ecf0f1")
            dist_lbl.pack(pady=10)
            k_lbl = tk.Label(f, text="k: 0.000", font=("Helvetica", 11), bg="#ecf0f1")
            k_lbl.pack()
            if key == "top": self.lbl_dist_top, self.lbl_k_top = dist_lbl, k_lbl
            else: self.lbl_dist_bot, self.lbl_k_bot = dist_lbl, k_lbl

        self.lbl_session = tk.Label(stats_container, text="Logged Points: 0", font=("Helvetica", 11), fg="#7f8c8d", bg="#ecf0f1")
        self.lbl_session.pack(side="bottom", pady=20)

        # TAB 2: TELEMETRY
        self.tab_tele = ttk.Frame(self.tabs)
        self.tabs.add(self.tab_tele, text=" 🛸 Dual Telemetry ")
        main_tele_frame = tk.Frame(self.tab_tele, bg="#ecf0f1")
        main_tele_frame.pack(fill="both", expand=True)

        self.tele_vars = {"top": {}, "bottom": {}}
        vars_to_show = ["A", "X", "TR", "BR", "B", "C", "L_Roll", "L_Tilt", "R_Roll", "R_Tilt"]

        for key, title, color in [("top", "TOP", "#e67e22"), ("bottom", "BOTTOM", "#9b59b6")]:
            col_frame = tk.LabelFrame(main_tele_frame, text=f" {title} DATA ", bg="#ecf0f1", fg=color, font=("Helvetica", 12, "bold"))
            col_frame.pack(side="left", fill="both", expand=True, padx=20, pady=20)
            for var in vars_to_show:
                f = tk.Frame(col_frame, bg="white", highlightbackground="#bdc3c7", highlightthickness=1)
                f.pack(fill="x", padx=10, pady=4)
                tk.Label(f, text=var, font=("Helvetica", 9, "bold"), bg="white", fg="#7f8c8d").pack(side="left", padx=5)
                v = tk.StringVar(value="0.000, 0.000, 0.000")
                tk.Label(f, textvariable=v, font=("Courier", 10), bg="white").pack(side="right", padx=5)
                self.tele_vars[key][var] = v

        # NEW TAB 3: SETTINGS
        self.tab_settings = ttk.Frame(self.tabs)
        self.tabs.add(self.tab_settings, text=" ⚙ Global Settings ")
        
        settings_canvas = tk.Frame(self.tab_settings, bg="#ecf0f1")
        settings_canvas.pack(fill="both", expand=True, padx=100, pady=50)

        tk.Label(settings_canvas, text="ArUco Physical Parameters", font=("Helvetica", 16, "bold"), bg="#ecf0f1", fg="#34495e").pack(pady=(0, 30))

        def create_size_control(parent, label, var, color):
            frame = tk.LabelFrame(parent, text=f" {label} Configuration ", bg="white", font=("Helvetica", 12), fg=color, padx=30, pady=20)
            frame.pack(fill="x", pady=15)
            
            inner = tk.Frame(frame, bg="white")
            inner.pack(fill="x")
            
            tk.Label(inner, text="Marker Size (mm):", bg="white", font=("Helvetica", 11)).pack(side="left")
            
            # Slider
            scale = tk.Scale(inner, from_=10.0, to=200.0, resolution=0.1, orient="horizontal", variable=var, bg="white", length=400, highlightthickness=0)
            scale.pack(side="left", padx=30)
            
            # Direct Entry
            entry = tk.Entry(inner, textvariable=var, width=8, font=("Helvetica", 12), justify="center")
            entry.pack(side="left")

        create_size_control(settings_canvas, "Upper Pair (Orange)", self.size_top, "#e67e22")
        create_size_control(settings_canvas, "Down Pair (Purple)", self.size_bot, "#9b59b6")

        tk.Label(settings_canvas, text="Note: Adjusting these values will instantly update the 3D depth calculations.", font=("Helvetica", 10, "italic"), bg="#ecf0f1", fg="#7f8c8d").pack(pady=40)

    def load_calib(self):
        if os.path.exists(CALIB_FILE):
            d = np.load(CALIB_FILE)
            return d['camera_matrix'], d['dist_coeff']
        return np.array([[RESOLUTION[0], 0, RESOLUTION[0]/2], [0, RESOLUTION[0], RESOLUTION[1]/2], [0, 0, 1]], dtype=np.float32), np.zeros(5)

    def measurement_loop(self):
        try:
            from picamera2 import Picamera2
            picam2 = Picamera2(); picam2.configure(picam2.create_video_configuration(main={"size": RESOLUTION, "format": "RGB888"})); picam2.start()
        except: return

        K, dist = self.load_calib()
        detector = cv.aruco.ArucoDetector(cv.aruco.getPredefinedDictionary(ARUCO_DICT), cv.aruco.DetectorParameters())
        log.init_log()

        buffers = {"top": [], "bottom": []}
        l_sample = l_update = time.time() * 1000

        while self.is_running:
            frame_rgb = picam2.capture_array()
            frame = cv.cvtColor(frame_rgb, cv.COLOR_RGB2BGR)
            corners, ids, _ = detector.detectMarkers(cv.cvtColor(frame, cv.COLOR_BGR2GRAY))
            curr = time.time() * 1000

            if ids is not None and len(ids) >= 2:
                marker_data = [{"corners": corners[i][0], "y": np.mean(corners[i][0], axis=0)[1]} for i in range(len(ids))]
                top_m = [m for m in marker_data if m["y"] < RESOLUTION[1]/2]
                bot_m = [m for m in marker_data if m["y"] >= RESOLUTION[1]/2]

                def process_pair(marker_list, key, size_mm):
                    if len(marker_list) < 2: return
                    marker_list.sort(key=lambda m: np.mean(m["corners"], axis=0)[0])
                    
                    def solve_p(c_pts):
                        h = size_mm / 2.0
                        obj = np.array([[-h, h, 0], [h, h, 0], [h, -h, 0], [-h, -h, 0]], dtype=np.float32)
                        _, rv, tv = cv.solvePnP(obj, c_pts, K, dist)
                        R, _ = cv.Rodrigues(rv)
                        pts = np.array([np.dot(R, pt) + tv.ravel() for pt in obj])
                        return {"pts": pts, "r": math.degrees(math.atan2(R[1,0], R[0,0])), "t": math.degrees(math.acos(np.clip(R[2,2], -1,1)))}

                    L = solve_p(marker_list[0]["corners"]); R = solve_p(marker_list[1]["corners"])
                    trl, brl = L["pts"][L["pts"][:, 0].argsort()[::-1]][:2][L["pts"][L["pts"][:, 0].argsort()[::-1]][:2][:, 1].argsort()]
                    tll = L["pts"][L["pts"][:, 0].argsort()[::-1]][2:][L["pts"][L["pts"][:, 0].argsort()[::-1]][2:][:, 1].argsort()][0]
                    b, c = R["pts"][R["pts"][:, 0].argsort()][:2][R["pts"][R["pts"][:, 0].argsort()][:2][:, 1].argsort()]
                    buffers[key].append({"A":(trl+brl)/2, "B":b, "C":c, "TR":trl, "BR":brl, "TL":tll, "L_Ang":(L["r"], L["t"]), "R_Ang":(R["r"], R["t"])})

                if (curr - l_sample) >= 100:
                    # FETCH DYNAMIC SIZES FROM GUI
                    process_pair(top_m, "top", self.size_top.get())
                    process_pair(bot_m, "bottom", self.size_bot.get())
                    l_sample = curr

                if (curr - l_update) >= 500:
                    for key in ["top", "bottom"]:
                        if len(buffers[key]) > 0:
                            aA, aB, aC, aTR, aBR, aTL = [np.mean([s[k] for s in buffers[key]], axis=0) for k in ["A", "B", "C", "TR", "BR", "TL"]]
                            aL, aR = np.mean([s["L_Ang"] for s in buffers[key]], axis=0), np.mean([s["R_Ang"] for s in buffers[key]], axis=0)
                            v_vec = (aTR-aTL)/np.linalg.norm(aTR-aTL); w_vec, u_vec = aC-aB, aB-aA
                            den = (np.dot(v_vec,v_vec)*np.dot(w_vec,w_vec))-(np.dot(v_vec,w_vec)**2)
                            if abs(den)>1e-6:
                                k = ((np.dot(v_vec,w_vec)*np.dot(u_vec,v_vec))-(np.dot(v_vec,v_vec)*np.dot(u_vec,w_vec)))/den
                                aX = aB + k * w_vec
                                dist_v = np.linalg.norm(aX-aA)
                                self.last_data[key].update({"A":aA, "X":aX, "TR":aTR, "BR":aBR, "B":aB, "C":aC, "dist":dist_v, "k":k, "L_Ang":aL, "R_Ang":aR})
                                self.last_data["session_count"] += 1
                                log.record(dist_v, k, aA, aX, aTR, aBR, aB, aC, u_vec, v_vec, w_vec, aL, aR)
                            buffers[key].clear()
                    l_update = curr
            else:
                self.last_data["top"]["dist"] = self.last_data["bottom"]["dist"] = 0.0

            for key, color in [("top", (0, 165, 255)), ("bottom", (255, 0, 255))]:
                if self.last_data[key]["dist"] > 0:
                    p_disp, _ = cv.projectPoints(np.array([self.last_data[key]["A"], self.last_data[key]["X"]]), np.zeros(3), np.zeros(3), K, dist)
                    p1, p2 = tuple(p_disp[0].ravel().astype(int)), tuple(p_disp[1].ravel().astype(int))
                    cv.line(frame, p1, p2, color, 3); cv.circle(frame, p2, 6, (0, 255, 0), -1)
                    cv.putText(frame, f"{key.upper()}: {self.last_data[key]['dist']:.2f}mm", (p1[0], p1[1]-10), 0, 0.6, color, 2)
            
            log.save_image(frame)
            self.current_frame = cv.cvtColor(frame, cv.COLOR_BGR2RGB)

    def update_gui_loop(self):
        if self.current_frame is not None:
            img = Image.fromarray(self.current_frame).resize((960, 540))
            self.tk_img = ImageTk.PhotoImage(image=img)
            self.canvas.create_image(0, 0, anchor="nw", image=self.tk_img)

        self.lbl_dist_top.config(text=f"{self.last_data['top']['dist']:.3f} mm")
        self.lbl_k_top.config(text=f"k: {self.last_data['top']['k']:.4f}")
        self.lbl_dist_bot.config(text=f"{self.last_data['bottom']['dist']:.3f} mm")
        self.lbl_k_bot.config(text=f"k: {self.last_data['bottom']['k']:.4f}")
        self.lbl_session.config(text=f"Total Measurements Recorded: {self.last_data['session_count']}")

        for key in ["top", "bottom"]:
            d = self.last_data[key]
            for var in ["A", "X", "TR", "BR", "B", "C"]:
                v = d[var]; self.tele_vars[key][var].set(f"{v[0]:.2f}, {v[1]:.2f}, {v[2]:.2f}")
            self.tele_vars[key]["L_Roll"].set(f"{d['L_Ang'][0]:.2f}°")
            self.tele_vars[key]["L_Tilt"].set(f"{d['L_Ang'][1]:.2f}°")
            self.tele_vars[key]["R_Roll"].set(f"{d['R_Ang'][0]:.2f}°")
            self.tele_vars[key]["R_Tilt"].set(f"{d['R_Ang'][1]:.2f}°")

        self.root.after(50, self.update_gui_loop)

if __name__ == "__main__":
    root = tk.Tk(); app = MeasurementApp(root); root.mainloop()
