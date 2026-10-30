import os, sys, json, time, threading, queue, statistics, shutil
from pathlib import Path
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import cv2
import mediapipe as mp
import pygame
from PIL import Image, ImageTk

DATA = Path(os.getenv('LOCALAPPDATA', str(Path.home()))) / 'AmpyGaze'
DATA.mkdir(parents=True, exist_ok=True)
CONFIG = DATA / 'settings.json'

class Trigger:
    def __init__(self):
        self.active = False
        self.since = None
    def update(self, value, threshold, delay, now):
        if value is None or value < threshold - .035:
            self.active = False
            self.since = None
        elif not self.active:
            if value >= threshold:
                if self.since is None: self.since = now
                if now - self.since >= delay: self.active = True
            else: self.since = None
        return self.active


def eye_measure(points, ids, iris):
    import numpy as np
    a, b, top, bottom = [np.array((points[i].x, points[i].y)) for i in ids]
    c = np.array((points[iris].x, points[iris].y))
    axis = bottom - top
    width = float(np.linalg.norm(b-a))
    height = float(np.linalg.norm(axis))
    if width < .005 or height / width < .12: return None
    return float(np.dot(c-top, axis) / np.dot(axis, axis))

class App:
    def __init__(self, root):
        self.root = root
        root.title('AmpyGaze — controllo dello sguardo')
        root.geometry('880x790')
        try: self.cfg = json.loads(CONFIG.read_text(encoding='utf-8'))
        except (OSError, ValueError): self.cfg = {}
        self.events = queue.Queue(maxsize=2)
        self.stop_event = threading.Event()
        self.worker = None
        self.trigger = Trigger()
        self.value = None
        self.calibration = None
        self.samples = []
        self.neutral = self.cfg.get('neutral')
        self.low = self.cfg.get('low')
        self.playing = False
        self.audio_ok = False
        self.threshold = tk.DoubleVar(value=self.cfg.get('threshold', .65))
        self.delay = tk.DoubleVar(value=self.cfg.get('delay', .35))
        self.camera = tk.IntVar(value=self.cfg.get('camera', 0))
        self.status = tk.StringVar(value='Pronto. Seleziona un MP3, avvia e calibra.')
        self.audio_label = tk.StringVar(value=self.cfg.get('audio', 'Nessun MP3 selezionato'))
        self.auto = tk.BooleanVar(value=self.autostart_enabled())
        frame = ttk.Frame(root, padding=16); frame.pack(fill='both', expand=True)
        ttk.Label(frame, text='AmpyGaze', font=('Segoe UI', 25, 'bold')).pack(anchor='w')
        ttk.Label(frame, text='Sguardo abbassato → MP3 · Sguardo alzato → silenzio').pack(anchor='w')
        self.preview = ttk.Label(frame); self.preview.pack(pady=10)
        bar = ttk.Frame(frame); bar.pack(fill='x')
        ttk.Button(bar, text='Carica MP3', command=self.choose_audio).pack(side='left')
        ttk.Label(bar, textvariable=self.audio_label, wraplength=620).pack(side='left', padx=10)
        controls = ttk.Frame(frame); controls.pack(fill='x', pady=10)
        ttk.Label(controls, text='Webcam n.').pack(side='left')
        ttk.Spinbox(controls, from_=0, to=9, textvariable=self.camera, width=3).pack(side='left', padx=6)
        ttk.Button(controls, text='Avvia', command=self.start).pack(side='left', padx=4)
        ttk.Button(controls, text='Pausa', command=self.stop).pack(side='left', padx=4)
        ttk.Button(controls, text='Prova MP3', command=self.test_audio).pack(side='left', padx=4)
        ttk.Button(controls, text='Ferma audio', command=self.silence).pack(side='left', padx=4)
        calib = ttk.Frame(frame); calib.pack(fill='x')
        ttk.Button(calib, text='1. Calibra sguardo alzato (3 s)', command=lambda:self.calibrate('neutral')).pack(side='left', padx=4)
        ttk.Button(calib, text='2. Calibra sguardo abbassato (3 s)', command=lambda:self.calibrate('low')).pack(side='left', padx=4)
        ttk.Label(frame, text='Soglia sguardo: aumenta per richiedere uno sguardo più basso.').pack(anchor='w', pady=(12,0))
        ttk.Scale(frame, from_=.1, to=.95, variable=self.threshold).pack(fill='x')
        row=ttk.Frame(frame); row.pack(fill='x', pady=6)
        ttk.Label(row, text='Ritardo attivazione (secondi):').pack(side='left')
        ttk.Spinbox(row, from_=.1, to=3, increment=.1, textvariable=self.delay, width=6).pack(side='left', padx=8)
        ttk.Checkbutton(frame, text='Avvia e attiva il controllo all’accesso a Windows', variable=self.auto, command=self.set_autostart).pack(anchor='w', pady=8)
        ttk.Label(frame, textvariable=self.status, wraplength=820, font=('Segoe UI', 11, 'bold')).pack(anchor='w', pady=8)
        ttk.Label(frame, text='Elaborazione locale: nessun video salvato o trasmesso. Se il volto scompare o gli occhi si chiudono, l’audio si ferma.\nChiudere la finestra termina l’app; minimizzarla mantiene il controllo attivo.', wraplength=820).pack(anchor='w')
        root.protocol('WM_DELETE_WINDOW', self.close)
        root.after(40, self.poll)
        if '--autostart' in sys.argv: root.after(1000, self.start)

    def save(self):
        self.cfg.update(threshold=self.threshold.get(), delay=self.delay.get(), camera=self.camera.get(), neutral=self.neutral, low=self.low)
        tmp = CONFIG.with_suffix('.tmp'); tmp.write_text(json.dumps(self.cfg), encoding='utf-8'); tmp.replace(CONFIG)
    def startup_file(self):
        return Path(os.environ['APPDATA']) / 'Microsoft/Windows/Start Menu/Programs/Startup/AmpyGaze.vbs'
    def autostart_enabled(self):
        return os.name == 'nt' and (self.startup_file().exists() or self.startup_file().with_suffix('.lnk').exists())
    def set_autostart(self):
        try:
            p=self.startup_file()
            p.with_suffix('.lnk').unlink(missing_ok=True)
            if self.auto.get():
                import subprocess
                executable = sys.executable
                if not getattr(sys, 'frozen', False):
                    executable = str(Path(sys.executable).with_name('pythonw.exe'))
                    args=[executable, str(Path(__file__).resolve()), '--autostart']
                else: args=[executable, '--autostart']
                command=subprocess.list2cmdline(args).replace('"','""')
                p.write_text('Set sh = CreateObject("WScript.Shell")\nsh.Run "'+command+'", 0, False\n', encoding='utf-16')
            else: p.unlink(missing_ok=True)
            self.save()
        except Exception as e:
            self.auto.set(self.autostart_enabled()); messagebox.showerror('Avvio automatico', str(e))
    def init_audio(self):
        if not self.audio_ok: pygame.mixer.init(); self.audio_ok=True
    def choose_audio(self):
        p=filedialog.askopenfilename(filetypes=[('Audio MP3','*.mp3')])
        if not p: return
        try:
            self.silence(); self.init_audio()
            target=DATA/'selected.mp3'
            pygame.mixer.music.unload()
            if Path(p).resolve()!=target.resolve(): shutil.copy2(p,target)
            pygame.mixer.music.load(str(target))
            self.cfg['audio']=str(target); self.audio_label.set(Path(p).name); self.save()
        except Exception as e: messagebox.showerror('MP3',str(e))
    def play(self):
        if self.playing: return
        try:
            self.init_audio(); pygame.mixer.music.load(self.cfg.get('audio',''))
            pygame.mixer.music.play(-1); self.playing=True
        except Exception as e:
            self.stop(); messagebox.showerror('Riproduzione MP3',str(e))
    def silence(self):
        if self.audio_ok: pygame.mixer.music.stop()
        self.playing=False
    def test_audio(self):
        if self.worker and self.worker.is_alive():
            messagebox.showinfo('Prova audio','Premi Pausa prima di provare il file.'); return
        self.play(); self.root.after(3000, self.silence)
    def start(self):
        if self.worker and self.worker.is_alive(): return
        if not Path(self.cfg.get('audio','')).is_file():
            self.status.set('Seleziona prima un MP3.'); return
        try: self.save()
        except Exception as e: messagebox.showerror('Impostazioni',str(e)); return
        self.stop_event.clear(); self.trigger=Trigger()
        self.worker=threading.Thread(target=self.capture, args=(self.camera.get(),), daemon=True); self.worker.start()
        self.status.set('Apertura webcam…')
    def stop(self):
        self.stop_event.set(); self.silence(); self.trigger=Trigger(); self.value=None
        self.calibration=None; self.status.set('In pausa — webcam in chiusura.')
    def capture(self, index):
        cap=None
        def send(item):
            try: self.events.put_nowait(item)
            except queue.Full:
                try: self.events.get_nowait()
                except queue.Empty: pass
                try: self.events.put_nowait(item)
                except queue.Full: pass
        try:
            cap=cv2.VideoCapture(index, cv2.CAP_DSHOW if os.name=='nt' else cv2.CAP_ANY)
            cap.set(cv2.CAP_PROP_FRAME_WIDTH,640); cap.set(cv2.CAP_PROP_FRAME_HEIGHT,480)
            if not cap.isOpened(): raise RuntimeError('Webcam non disponibile. Controlla numero, permessi e altre app.')
            with mp.solutions.face_mesh.FaceMesh(max_num_faces=1, refine_landmarks=True, min_detection_confidence=.6, min_tracking_confidence=.6) as mesh:
                while not self.stop_event.is_set():
                    ok, frame=cap.read()
                    if not ok: raise RuntimeError('La webcam non restituisce immagini.')
                    rgb=cv2.cvtColor(frame,cv2.COLOR_BGR2RGB)
                    result=mesh.process(rgb); score=None
                    if result.multi_face_landmarks:
                        pts=result.multi_face_landmarks[0].landmark
                        scores=[eye_measure(pts,(33,133,159,145),468),eye_measure(pts,(362,263,386,374),473)]
                        if all(x is not None for x in scores): score=sum(scores)/2
                        for i in (468,473):
                            cv2.circle(rgb,(int(pts[i].x*rgb.shape[1]),int(pts[i].y*rgb.shape[0])),3,(0,255,150),-1)
                    send(('frame', rgb[:,::-1].copy(), score, time.monotonic()))
        except Exception as e: send(('error',str(e)))
        finally:
            if cap: cap.release()
    def calibrate(self, kind):
        if not self.worker or not self.worker.is_alive() or self.stop_event.is_set():
            messagebox.showinfo('Calibrazione','Avvia prima la webcam.'); return
        self.calibration=(kind,time.monotonic()+3); self.samples=[]; self.silence(); self.trigger=Trigger()
    def poll(self):
        try:
            while True:
                item=self.events.get_nowait()
                if item[0]=='error': self.stop(); self.status.set(item[1]); continue
                _, rgb, score, stamp=item
                if self.stop_event.is_set(): continue
                photo=ImageTk.PhotoImage(Image.fromarray(rgb).resize((560,420)))
                self.preview.configure(image=photo); self.preview.image=photo
                self.value=score; self.last_frame=stamp
                if self.calibration:
                    kind,end=self.calibration
                    if score is not None: self.samples.append(score)
                    self.status.set('Mantieni lo sguardo '+('alzato' if kind=='neutral' else 'abbassato')+'…')
                    if time.monotonic()>=end:
                        self.calibration=None
                        if len(self.samples)<12: self.status.set('Calibrazione fallita: occhi non rilevati abbastanza.'); continue
                        if kind=='neutral': self.neutral=statistics.median(self.samples)
                        else: self.low=statistics.median(self.samples)
                        if self.neutral is not None and self.low is not None:
                            if self.low-self.neutral<.04:
                                self.status.set('Sguardi troppo simili: ripeti la calibrazione e migliora la luce.'); continue
                            self.threshold.set((self.low+self.neutral)/2)
                        self.save(); self.status.set('Calibrazione salvata.')
                    continue
                active=self.trigger.update(score,self.threshold.get(),self.delay.get(),stamp)
                if active: self.play()
                else: self.silence()
                self.status.set('Occhi non rilevati / chiusi — silenzio' if score is None else f'{"Sguardo basso — MP3 attivo" if active else "Sguardo alzato — silenzio"}  |  Valore {score:.2f} · Soglia {self.threshold.get():.2f}')
        except queue.Empty: pass
        if self.playing and self.worker and time.monotonic()-getattr(self,'last_frame',0)>.7:
            self.silence(); self.trigger=Trigger(); self.status.set('Webcam senza aggiornamenti — audio fermato.')
        self.root.after(40,self.poll)
    def close(self):
        self.stop()
        try: self.save()
        except Exception: pass
        self.root.destroy()

if __name__=='__main__':
    root=tk.Tk(); App(root); root.mainloop()
