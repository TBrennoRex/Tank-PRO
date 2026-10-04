from __future__ import annotations
import os, sys, time, threading, re
from pathlib import Path
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

from protocol import *

if os.name != 'nt':
    raise SystemExit('Este aplicativo usa Windows.Devices.Midi e deve ser executado no Windows.')
from winrt_midi import list_ports, MidiLink, Port

KPT_NAMES=('usb-midi','sinco-midi','kpt','m-vave','yuimer')


def likely_kpt(port):
    n=port.name.lower()
    return any(x in n for x in KPT_NAMES) or ('midi' in n)


def handshake(link, attempts=3):
    link.drain()
    for _ in range(attempts):
        link.send(HS_QUERY)
        end=time.time()+1.5
        while time.time()<end:
            p=link.recv(max(0.05,end-time.time()))
            ident=parse_identity(p) if p else None
            if ident:return ident,p
    return None,None


def discover(logger):
    ports=list_ports(logger); outs=[p for p in ports if p.direction=='OUT' and likely_kpt(p)]; ins=[p for p in ports if p.direction=='IN' and likely_kpt(p)]
    if not outs or not ins: raise RuntimeError('Nenhuma porta MIDI compatível foi encontrada.')
    for p in ports: logger(f'{p.direction}: {p}')
    pairs=[]
    for o in outs:
        for i in ins:
            score=0
            if o.name.lower()==i.name.lower():score+=5
            if 'sinco' in o.name.lower() and 'sinco' in i.name.lower():score+=3
            pairs.append((score,o,i))
    pairs.sort(key=lambda x:x[0],reverse=True)
    for _,o,i in pairs:
        link=None
        try:
            logger(f'Handshake OUT {o} / IN {i}')
            link=MidiLink(o,i); ident,pkt=handshake(link)
            if pkt and not ident:
                logger(f'  SysEx recebido, identidade não decodificada: {pkt.hex(" ")}')
            link.close()
            if ident:
                logger(f'  identidade: {ident.get("model")}_{ident.get("version")}')
                return o,i,ident
        except Exception as e:
            logger(f'  falhou: {e}')
            try:
                if link:link.close()
            except Exception:pass
    raise RuntimeError('Nenhuma porta respondeu ao handshake KPT PRO.')


def recovery_ports(logger):
    """Select the physical USB-MIDI pair without requiring normal identity."""
    ports=list_ports(logger)
    for p in ports: logger(f'{p.direction}: {p}')
    def device_port(p):
        n=p.name.lower()
        return likely_kpt(p) and 'wavetable' not in n and 'synth' not in n
    outs=[p for p in ports if p.direction=='OUT' and device_port(p)]
    ins=[p for p in ports if p.direction=='IN' and device_port(p)]
    if not outs or not ins:
        raise RuntimeError('USB-Midi não foi encontrado para recuperação.')
    pairs=[]
    for o in outs:
        for i in ins:
            score=(5 if o.name.lower()==i.name.lower() else 0)
            if 'usb-midi' in o.name.lower() and 'usb-midi' in i.name.lower(): score+=4
            if 'sinco' in o.name.lower() and 'sinco' in i.name.lower(): score+=3
            pairs.append((score,o,i))
    _,o,i=max(pairs,key=lambda x:x[0])
    logger(f'Recuperação sem handshake: OUT {o} / IN {i}')
    return o,i


def serve(link, logical, done_addr, idle, phase, logger, progress, stop):
    count=0; last=time.time()
    while not stop.is_set():
        pkt=link.recv(1.0)
        if pkt is None:
            if time.time()-last>idle: return False,count,'timeout'
            continue
        req=parse_request(pkt)
        if not req: continue
        fl,addr,n=req; last=time.time()
        logger(f'REQ {phase}: type={fl:#x} addr=0x{addr:08X} len={n}')
        if addr==done_addr:
            link.send(build_success(addr)); logger(f'DONE {addr:#x}')
            return True,count,'done'
        if n>MAX_CHUNK: raise RuntimeError(f'Pedido > {MAX_CHUNK} bytes: {n}')
        if addr+n>len(logical): raise RuntimeError(f'Pedido fora da imagem: 0x{addr:X}+{n} > 0x{len(logical):X}')
        time.sleep(0.010)
        link.send(build_response(addr,logical[addr:addr+n],fl)); count+=1
        progress(min(100,100*(addr+n)/len(logical)),f'{phase}: {count} blocos')
    return False,count,'cancelled'


def serve_recovery(link, logical, idle, phase, logger, progress, stop):
    """Serve either recovery marker; no identity response is required."""
    count=0; last=time.time()
    while not stop.is_set():
        pkt=link.recv(1.0)
        if pkt is None:
            if time.time()-last>idle: return None,count,'timeout'
            continue
        req=parse_request(pkt)
        if not req: continue
        fl,addr,n=req; last=time.time()
        logger(f'REQ {phase}: type={fl:#x} addr=0x{addr:08X} len={n}')
        if addr in (DONE_VERIFY,DONE_UPGRADE):
            link.send(build_success(addr)); logger(f'DONE {addr:#x}')
            return addr,count,'done'
        if n>MAX_CHUNK: raise RuntimeError(f'Pedido > {MAX_CHUNK} bytes: {n}')
        if addr+n>len(logical): raise RuntimeError(f'Pedido fora da imagem: 0x{addr:X}+{n} > 0x{len(logical):X}')
        time.sleep(0.010)
        link.send(build_response(addr,logical[addr:addr+n],fl)); count+=1
        progress(min(100,100*(addr+n)/len(logical)),f'{phase}: {count} blocos')
    return None,count,'cancelled'

class App(tk.Tk):
    def __init__(self):
        super().__init__(); self.title('KPT PRO Native Updater'); self.geometry('1020x720'); self.minsize(900,620)
        self.configure(bg='#11161C'); self.stop=threading.Event(); self.build()
    def build(self):
        st=ttk.Style(self); st.theme_use('clam'); st.configure('TFrame',background='#11161C'); st.configure('TLabel',background='#11161C',foreground='#E8EEF5'); st.configure('TButton',padding=(10,7)); st.configure('Title.TLabel',font=('Segoe UI',21,'bold'),foreground='#FFFFFF'); st.configure('Sub.TLabel',foreground='#9EA9B6'); st.configure('Horizontal.TProgressbar',troughcolor='#26313D',background='#3A7BEE')
        top=ttk.Frame(self,padding=18); top.pack(fill='x'); ttk.Label(top,text='KPT PRO Native Updater',style='Title.TLabel').pack(anchor='w'); ttk.Label(top,text='Updater independente — sem M-UPGRADE — USB-MIDI/SysEx',style='Sub.TLabel').pack(anchor='w')
        main=ttk.Frame(self,padding=(18,0,18,12)); main.pack(fill='both',expand=True)
        left=ttk.Frame(main); left.pack(side='left',fill='y',padx=(0,12)); right=ttk.Frame(main); right.pack(side='left',fill='both',expand=True)
        self.file=tk.StringVar(); ttk.Label(left,text='Firmware .fwsc').pack(anchor='w'); e=ttk.Entry(left,textvariable=self.file,width=45); e.pack(fill='x',pady=(5,8)); ttk.Button(left,text='Selecionar...',command=self.pick).pack(fill='x'); ttk.Button(left,text='Analisar',command=self.analyze).pack(fill='x',pady=6); ttk.Separator(left).pack(fill='x',pady=8); ttk.Button(left,text='Procurar KPT PRO',command=self.scan).pack(fill='x'); ttk.Button(left,text='Recuperação V9',command=self.recover_v9).pack(fill='x',pady=6); ttk.Button(left,text='Flash / Atualizar',command=self.flash).pack(fill='x'); ttk.Button(left,text='Parar',command=self.stop.set).pack(fill='x');
        self.info=tk.Text(left,height=20,width=45,bg='#0A0E13',fg='#DDE6EF',relief='flat'); self.info.pack(fill='both',expand=True,pady=(10,0))
        nb=ttk.Notebook(right); nb.pack(fill='both',expand=True); tab1=ttk.Frame(nb); tab2=ttk.Frame(nb); nb.add(tab1,text='Dispositivo'); nb.add(tab2,text='Log');
        self.device=tk.Text(tab1,bg='#0A0E13',fg='#DDE6EF',relief='flat'); self.device.pack(fill='both',expand=True)
        self.logbox=tk.Text(tab2,bg='#070A0D',fg='#BFCBD8',relief='flat'); self.logbox.pack(fill='both',expand=True)
        self.status=tk.StringVar(value='Pronto — nenhuma gravação iniciada'); ttk.Label(self,textvariable=self.status,padding=(18,5)).pack(fill='x'); self.pb=ttk.Progressbar(self,maximum=100); self.pb.pack(fill='x',padx=18,pady=(0,14))
    def log(self,s): self.after(0,lambda:(self.logbox.insert('end',time.strftime('[%H:%M:%S] ')+s+'\n'),self.logbox.see('end')))
    def prog(self,p,s): self.after(0,lambda:(self.pb.configure(value=p),self.status.set(s)))
    def pick(self):
        p=filedialog.askopenfilename(filetypes=[('KPT PRO firmware','*.fwsc'),('All files','*.*')]);
        if p:self.file.set(p); self.analyze()
    def setinfo(self,s): self.info.delete('1.0','end'); self.info.insert('1.0',s)
    def analyze(self):
        try:
            inf,logical=firmware_info(self.file.get()); self.setinfo(f'MODELO: KPTPRO\nVERSÃO: {inf.version}\n\n.fwsc: {inf.raw_size:,} bytes\nLógico: {inf.logical_size:,} bytes\nFlash KPT conhecido: {KPT_FLASH_SIZE:,} bytes\n\nSHA-256:\n{inf.raw_sha256}\n\nImagem lógica SHA-256:\n{inf.logical_sha256}\n'); self.status.set('FWSC KPT PRO reconhecido')
        except Exception as e:self.setinfo('ERRO\n'+str(e)); self.status.set('Firmware não reconhecido')
    def scan(self):
        def w():
            try:
                o,i,ident=discover(self.log); self.after(0,lambda:self.device.delete('1.0','end')); self.after(0,lambda:self.device.insert('end',f'OUT: {o}\nIN:  {i}\n\nModelo: {ident["model"]}\nVersão: {ident.get("version")}\nModo OTA: {ident.get("ota")}\n')) ; self.after(0,lambda:self.status.set('KPT PRO detectado'))
            except Exception as e:self.log(str(e)); self.after(0,lambda:self.status.set('KPT PRO não respondeu'))
        threading.Thread(target=w,daemon=True).start()
    def recover_v9(self):
        p=self.file.get().strip()
        if not p:return messagebox.showerror('KPT PRO','Selecione o KPTPRO_009.fwsc.')
        try:
            inf,_=firmware_info(p)
            if inf.version!=9: raise ValueError('A recuperação controlada aceita somente o firmware KPT PRO V9.')
        except Exception as e:
            return messagebox.showerror('KPT PRO',str(e))
        warning=('RECUPERAÇÃO KPT PRO V9\n\n'
                  'A pedaleira pode estar sem display e sem responder ao handshake normal.\n'
                  'O app enviará o comando OTA e servirá somente o KPTPRO_009.fwsc.\n\n'
                  'Não desconecte USB nem interrompa a energia.\n\nContinuar?')
        if not messagebox.askyesno('CONFIRMAR RECUPERAÇÃO',warning): return
        self.stop.clear()
        def w():
            link=None
            try:
                inf,logical=firmware_info(p)
                o,i=recovery_ports(self.log)
                link=MidiLink(o,i)
                self.log('Enviando comando de entrada OTA (sem handshake normal)...')
                link.drain(); link.send(UPGRADE_CMD); time.sleep(2)
                marker,n,reason=serve_recovery(link,logical,30,'RECUP V9',self.log,self.prog,self.stop)
                if reason!='done': raise RuntimeError(f'Nenhuma requisição OTA recebida: {reason}')
                if marker==DONE_VERIFY:
                    self.log('Etapa de verificação concluída; aguardando reenumeração OTA...')
                    link.close(); link=None
                    deadline=time.time()+30; oo=ii=None
                    while time.time()<deadline and not self.stop.is_set():
                        try: oo,ii=recovery_ports(self.log); break
                        except Exception: time.sleep(.75)
                    if not oo: raise RuntimeError('Loader OTA não reapareceu.')
                    link=MidiLink(oo,ii); link.drain(); link.send(UPGRADE_CMD); time.sleep(2)
                    marker,n,reason=serve_recovery(link,logical,180,'RECUP V9 FLASH',self.log,self.prog,self.stop)
                    if marker!=DONE_UPGRADE: raise RuntimeError(f'Etapa final não concluída: {reason}')
                self.prog(100,'Recuperação V9 concluída')
                self.after(0,lambda:messagebox.showinfo('KPT PRO','A imagem V9 foi enviada. Aguarde o reboot e reconecte a pedaleira.'))
            except Exception as e:
                self.log('ERRO RECUPERAÇÃO: '+str(e)); self.after(0,lambda:messagebox.showerror('KPT PRO',str(e))); self.after(0,lambda:self.status.set('Falha na recuperação'))
            finally:
                try:
                    if link: link.close()
                except Exception: pass
        threading.Thread(target=w,daemon=True).start()
    def flash(self):
        p=self.file.get().strip()
        if not p:return messagebox.showerror('KPT PRO','Selecione o .fwsc.')
        if not messagebox.askyesno('CONFIRMAR','Isto grava o firmware no KPT PRO.\n\nO M-UPGRADE não será usado.\n\nContinuar?'):return
        self.stop.clear()
        def w():
            try:
                inf,logical=firmware_info(p)
                o,i,id1=discover(self.log)
                if re.sub(r'[^A-Za-z0-9]', '', id1['model']).upper() not in ('KPTPRO', 'OTAKPTPRO'):
                    raise RuntimeError(f'Modelo inesperado: {id1["model"]}')
                self.log(f'Dispositivo KPTPRO v{id1.get("version")}')
                link=MidiLink(o,i)
                try:
                    ident,_=handshake(link); link.send(UPGRADE_CMD); time.sleep(2); ok,n,reason=serve(link,logical,DONE_VERIFY,10,'VERIF',self.log,self.prog,self.stop)
                    if not ok: raise RuntimeError(f'etapa 1 sem confirmação: {reason}')
                finally: link.close()
                self.log('Aguardando reenumeração do loader OTA...'); deadline=time.time()+30; ota=None
                while time.time()<deadline and not self.stop.is_set():
                    try:
                        oo,ii,iiid=discover(self.log)
                        if iiid and (iiid.get('ota') or str(iiid.get('model','')).lower().startswith('ota-kptpro')): ota=(oo,ii,iiid); break
                    except Exception:pass
                    time.sleep(.75)
                if not ota: raise RuntimeError('Loader OTA não reapareceu.')
                oo,ii,iiid=ota; self.log(f'Loader: {iiid}')
                link=MidiLink(oo,ii)
                try:
                    hand,_=handshake(link); link.send(UPGRADE_CMD); time.sleep(2); ok,n,reason=serve(link,logical,DONE_UPGRADE,180,'FLASH',self.log,self.prog,self.stop)
                    if not ok: raise RuntimeError(f'etapa 2 sem F0000000: {reason}')
                finally: link.close()
                self.log('Esperando reboot e verificação...'); deadline=time.time()+30; good=False
                while time.time()<deadline and not self.stop.is_set():
                    try:
                        oo,ii,iiid=discover(self.log)
                        if iiid and iiid['model']=='KPTPRO' and iiid.get('version')==inf.version: good=True; break
                    except Exception:pass
                    time.sleep(.75)
                if not good: raise RuntimeError('Flash terminou, mas a versão instalada não foi confirmada.')
                self.prog(100,'Concluído'); self.after(0,lambda:messagebox.showinfo('KPT PRO','Firmware gravado e verificado.'))
            except Exception as e:self.log('ERRO: '+str(e)); self.after(0,lambda:messagebox.showerror('KPT PRO',str(e))); self.after(0,lambda:self.status.set('Falha'))
        threading.Thread(target=w,daemon=True).start()

if __name__=='__main__': App().mainloop()
