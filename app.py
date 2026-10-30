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
    def update(self, value, threshold, delay, now, inverted=False):
        if inverted:
            value = -value if value is not None else None
            threshold = -threshold
        if value is None or value < threshold - .035:
            self.active = False
            self.since = None
        elif not self.active:
            if value >= threshold:
                if self.since is None: self.since = now
                if now - self.since >= delay: self.active = True
            else: self.since = None
        return self.active


class Monitor:
    def __init__(self):
        self.gaze = Trigger()
        self.closed_since = None
        self.absent_since = None
    def update(self, score, closed, face, threshold, gaze_delay, closed_delay, now, inverted=False, absent_delay=.35):
        if not face:
            self.gaze = Trigger()
            self.closed_since = None
            if self.absent_since is None: self.absent_since = now
            active = now - self.absent_since >= absent_delay
            return active, 'Volto assente — ' + ('MP3 attivo' if active else 'attesa')
        self.absent_since = None
        if closed:
            self.gaze = Trigger()
            if self.closed_since is None: self.closed_since = now
            active = now - self.closed_since >= closed_delay
            return active, 'Occhi chiusi — ' + ('MP3 attivo' if active else 'attesa')
        self.closed_since = None
        active = self.gaze.update(score, threshold, gaze_delay, now, inverted)
        if score is None: return False, 'Sguardo non rilevato — silenzio'
        return active, 'Sguardo basso — MP3 attivo' if active else 'Occhi aperti — silenzio'


class AudioGate:
    def __init__(self):
        self.active=False
        self.release_since=None
    def update(self, requested, now, release_delay):
        if requested:
            self.active=True
            self.release_since=None
        elif self.active:
            if self.release_since is None: self.release_since=now
            if now-self.release_since >= release_delay: self.active=False
        return self.active


def effective_threshold(threshold, sensitivity, inverted):
    offset=(sensitivity-50)*.003
    return threshold+offset if inverted else threshold-offset

def eye_openness(points, ids, frame_width, frame_height):
    import math
    def distance(i, j):
        return math.hypot((points[i].x-points[j].x)*frame_width,
                          (points[i].y-points[j].y)*frame_height)
    a,b,t1,b1,t2,b2 = ids
    width = distance(a,b)
    if width < 4: return None
    return (distance(t1,b1)+distance(t2,b2)) / (2*width)

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
        root.title('AmpyGaze Rev5 — controllo dello sguardo')
        root.geometry('920x890')
        try: self.cfg = json.loads(CONFIG.read_text(encoding='utf-8'))
        except (OSError, ValueError): self.cfg = {}
        self.events = queue.Queue(maxsize=2)
        self.stop_event = threading.Event()
        self.worker = None
        self.monitor = Monitor(); self.gate=AudioGate()
        self.value = None
        self.calibration = None
        self.samples = []
        self.neutral = self.cfg.get('neutral')
        self.low = self.cfg.get('low')
        self.inverted = tk.BooleanVar(value=self.cfg.get('inverted', True))
        self.playing = False
        self.audio_ok = False
        self.threshold = tk.DoubleVar(value=self.cfg.get('threshold', .65))
        self.delay = tk.DoubleVar(value=self.cfg.get('delay', .35))
        self.closed_delay = tk.DoubleVar(value=self.cfg.get('closed_delay', 1.0))
        self.absent_delay = tk.DoubleVar(value=self.cfg.get('absent_delay', .35))
        self.release_delay = tk.DoubleVar(value=self.cfg.get('release_delay', 0.0))
        self.sensitivity = tk.DoubleVar(value=self.cfg.get('sensitivity', 50))
        self.closed_threshold = tk.DoubleVar(value=self.cfg.get('closed_threshold', .14))
        self.camera = tk.IntVar(value=self.cfg.get('camera', 0))
        self.status = tk.StringVar(value='Pronto. Seleziona un MP3, avvia e calibra.')
        self.audio_label = tk.StringVar(value=self.cfg.get('audio', 'Nessun MP3 selezionato'))
        self.auto = tk.BooleanVar(value=self.autostart_enabled())
        canvas=tk.Canvas(root, highlightthickness=0)
        scroll=ttk.Scrollbar(root, orient='vertical', command=canvas.yview)
        scroll.pack(side='right', fill='y'); canvas.pack(side='left', fill='both', expand=True)
        canvas.configure(yscrollcommand=scroll.set)
        frame=ttk.Frame(canvas, padding=16)
        window=canvas.create_window((0,0), window=frame, anchor='nw')
        frame.bind('<Configure>', lambda e:canvas.configure(scrollregion=canvas.bbox('all')))
        canvas.bind('<Configure>', lambda e:canvas.itemconfigure(window, width=e.width))
        root.bind('<MouseWheel>', lambda e:canvas.yview_scroll(int(-e.delta/120), 'units'))
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
        ttk.Button(calib, text='Reset calibrazione', command=self.reset_calibration).pack(side='left', padx=4)
        ttk.Checkbutton(frame, text='Inverti direzione (la calibrazione imposta automaticamente il verso)', variable=self.inverted, command=self.change_direction).pack(anchor='w', pady=(8,0))
        ttk.Label(frame, text='Soglia sguardo: usa la calibrazione per impostare il punto di attivazione.').pack(anchor='w', pady=(12,0))
        ttk.Scale(frame, from_=.1, to=.95, variable=self.threshold).pack(fill='x')
        row=ttk.Frame(frame); row.pack(fill='x', pady=6)
        ttk.Label(frame, text='Sensibilità sguardo (più alta = si attiva con movimenti più piccoli)').pack(anchor='w')
        ttk.Scale(frame, from_=0, to=100, variable=self.sensitivity, command=lambda v:self.settings_changed()).pack(fill='x')
        for title, var in [('Sguardo basso (s)',self.delay),('Occhi chiusi (s)',self.closed_delay),('Volto assente (s)',self.absent_delay),('Arresto audio (s)',self.release_delay)]:
            cell=ttk.Frame(row); cell.pack(side='left', padx=6)
            ttk.Label(cell, text=title).pack(anchor='w')
            ttk.Spinbox(cell, from_=0, to=30, increment=.05, textvariable=var, width=8, command=self.settings_changed).pack(anchor='w')
        ttk.Button(frame, text='Applica impostazioni', command=self.apply_settings).pack(anchor='w', pady=4)
        ttk.Label(frame, text='Soglia occhi chiusi: riduci se scatta anche guardando in basso.').pack(anchor='w')
        ttk.Scale(frame, from_=.06, to=.25, variable=self.closed_threshold).pack(fill='x')
        ttk.Checkbutton(frame, text='Avvia e attiva il controllo all’accesso a Windows', variable=self.auto, command=self.set_autostart).pack(anchor='w', pady=8)
        ttk.Label(frame, textvariable=self.status, wraplength=820, font=('Segoe UI', 11, 'bold')).pack(anchor='w', pady=8)
        ttk.Label(frame, text='Elaborazione locale: nessun video salvato o trasmesso. Sguardo basso, occhi chiusi o volto assente: MP3 dopo i tempi impostati.\nChiudere la finestra termina l’app; minimizzarla mantiene il controllo attivo.', wraplength=820).pack(anchor='w')
        root.protocol('WM_DELETE_WINDOW', self.close)
        root.after(40, self.poll)
        if '--autostart' in sys.argv: root.after(1000, self.start)

    def settings_changed(self):
        self.monitor=Monitor(); self.gate=AudioGate()
        self.silence()

    def apply_settings(self):
        try:
            self.parameters()
            self.settings_changed()
            self.save()
            self.status.set('Sensibilità e tempi salvati.')
        except (ValueError, tk.TclError):
            messagebox.showerror('Impostazioni', 'Inserisci tempi da 0 a 30 secondi e sensibilità da 0 a 100.')

    def parameters(self):
        values=[v.get() for v in (self.delay,self.closed_delay,self.absent_delay,self.release_delay)]
        if not all(0 <= v <= 30 for v in values): raise ValueError('Tempi non validi')
        sensitivity=self.sensitivity.get()
        if not 0 <= sensitivity <= 100: raise ValueError('Sensibilità non valida')
        return (*values, effective_threshold(self.threshold.get(), sensitivity, self.inverted.get()))

    def reset_calibration(self):
        self.silence()
        self.calibration=None
        self.samples=[]
        self.neutral=None
        self.low=None
        self.threshold.set(.65)
        self.inverted.set(True)
        self.closed_threshold.set(.14)
        self.sensitivity.set(50)
        self.monitor=Monitor(); self.gate=AudioGate()
        self.save()
        self.status.set('Calibrazione azzerata. Ripeti le due calibrazioni dello sguardo.')

    def change_direction(self):
        self.silence()
        self.monitor=Monitor(); self.gate=AudioGate()
        self.save()

    def save(self):
        self.parameters()
        self.cfg.update(threshold=self.threshold.get(), delay=self.delay.get(), camera=self.camera.get(), neutral=self.neutral, low=self.low, inverted=self.inverted.get(), closed_threshold=self.closed_threshold.get(), closed_delay=self.closed_delay.get(), absent_delay=self.absent_delay.get(), release_delay=self.release_delay.get(), sensitivity=self.sensitivity.get())
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
        self.stop_event.clear(); self.monitor=Monitor(); self.gate=AudioGate()
        self.worker=threading.Thread(target=self.capture, args=(self.camera.get(),), daemon=True); self.worker.start()
        self.status.set('Apertura webcam…')
    def stop(self):
        self.stop_event.set(); self.silence(); self.monitor=Monitor(); self.gate=AudioGate(); self.value=None
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
                    result=mesh.process(rgb); score=None; openness=None; face=False
                    if result.multi_face_landmarks:
                        face=True
                        pts=result.multi_face_landmarks[0].landmark
                        eye_ratios=[eye_openness(pts, ids, rgb.shape[1], rgb.shape[0]) for ids in [(33,133,160,144,158,153),(362,263,385,380,387,373)]]
                        if all(v is not None for v in eye_ratios): openness=max(eye_ratios)
                        scores=[eye_measure(pts,(33,133,159,145),468),eye_measure(pts,(362,263,386,374),473)]
                        if all(x is not None for x in scores): score=sum(scores)/2
                        for i in (468,473):
                            cv2.circle(rgb,(int(pts[i].x*rgb.shape[1]),int(pts[i].y*rgb.shape[0])),3,(0,255,150),-1)
                    send(('frame', rgb[:,::-1].copy(), score, openness, face, time.monotonic()))
        except Exception as e: send(('error',str(e)))
        finally:
            if cap: cap.release()
    def calibrate(self, kind):
        if not self.worker or not self.worker.is_alive() or self.stop_event.is_set():
            messagebox.showinfo('Calibrazione','Avvia prima la webcam.'); return
        self.calibration=(kind,time.monotonic()+3); self.samples=[]; self.silence(); self.monitor=Monitor(); self.gate=AudioGate()
    def poll(self):
        try:
            while True:
                item=self.events.get_nowait()
                if item[0]=='error': self.stop(); self.status.set(item[1]); continue
                _, rgb, score, openness, face, stamp=item
                closed = openness is not None and openness < self.closed_threshold.get()
                if self.stop_event.is_set(): continue
                photo=ImageTk.PhotoImage(Image.fromarray(rgb).resize((480,360)))
                self.preview.configure(image=photo); self.preview.image=photo
                if time.monotonic()-stamp > .7:
                    self.silence(); self.monitor=Monitor(); self.gate=AudioGate(); continue
                if stamp-getattr(self, 'last_frame', stamp) > .7: self.monitor=Monitor(); self.gate=AudioGate()
                self.value=score; self.last_frame=stamp
                if self.calibration:
                    kind,end=self.calibration
                    if score is not None and not closed: self.samples.append(score)
                    self.status.set('Mantieni lo sguardo '+('alzato' if kind=='neutral' else 'abbassato')+'…')
                    if time.monotonic()>=end:
                        self.calibration=None
                        if len(self.samples)<12: self.status.set('Calibrazione fallita: occhi non rilevati abbastanza.'); continue
                        if kind=='neutral': self.neutral=statistics.median(self.samples)
                        else: self.low=statistics.median(self.samples)
                        if self.neutral is not None and self.low is not None:
                            if abs(self.low-self.neutral)<.04:
                                self.status.set('Sguardi troppo simili: ripeti la calibrazione e migliora la luce.'); continue
                            self.inverted.set(self.low < self.neutral)
                            self.monitor=Monitor(); self.gate=AudioGate()
                            self.threshold.set((self.low+self.neutral)/2)
                        self.save(); self.status.set('Calibrazione salvata.')
                    continue
                try: gaze_delay,closed_delay,absent_delay,release_delay,threshold=self.parameters()
                except (ValueError,tk.TclError):
                    self.silence(); self.monitor=Monitor(); self.gate=AudioGate()
                    self.status.set('Tempi non validi: inserisci valori da 0 a 30 e premi Applica.'); continue
                active, label=self.monitor.update(score,closed,face,threshold,gaze_delay,closed_delay,stamp,self.inverted.get(),absent_delay)
                active=self.gate.update(active,stamp,release_delay)
                if active: self.play()
                else: self.silence()
                self.status.set(label + (f' | Apertura occhi {openness:.2f}' if openness is not None else ''))
        except queue.Empty: pass
        if self.playing and self.worker and time.monotonic()-getattr(self,'last_frame',0)>.7:
            self.silence(); self.monitor=Monitor(); self.gate=AudioGate(); self.status.set('Webcam senza aggiornamenti — audio fermato.')
        self.root.after(40,self.poll)
    def close(self):
        self.stop()
        try: self.save()
        except Exception: pass
        self.root.destroy()

if __name__=='__main__':
    if '--self-test' in sys.argv:
        with mp.solutions.face_mesh.FaceMesh(max_num_faces=1, refine_landmarks=True):
            pass
        sys.exit(0)
    root=tk.Tk(); App(root); root.mainloop()
