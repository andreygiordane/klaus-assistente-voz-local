"""Varredura do computador: saúde do sistema e segurança. Só LEITURA (não altera nem apaga nada).
Devolve um relatório que o Klaus fala; a verificação de antivírus (Windows Defender) roda em segundo plano e ele avisa ao terminar."""
import json
import os
import re
import subprocess
import threading
import time
from datetime import datetime

import psutil

from . import config

SCRIPT = r"""
$ErrorActionPreference='SilentlyContinue'
$mp  = Get-MpComputerStatus | Select AntivirusEnabled,RealTimeProtectionEnabled,AntivirusSignatureAge,QuickScanAge,FullScanAge
$det = @(Get-MpThreatDetection | Sort InitialDetectionTime -Descending | Select -First 5 ThreatID,InitialDetectionTime,ActionSuccess,@{n='Recurso';e={($_.Resources -join ';')}})
$thr = @(Get-MpThreat | Select ThreatID,ThreatName,SeverityID,IsActive,DidThreatExecute)
$fw  = @(Get-NetFirewallProfile | Select Name,Enabled)
$dk  = @(Get-PhysicalDisk | Select FriendlyName,HealthStatus,OperationalStatus)
$ev  = @(Get-WinEvent -FilterHashtable @{LogName='System';Level=1,2;StartTime=(Get-Date).AddHours(-24)} -MaxEvents 400)
$evg = @($ev | Group-Object ProviderName | Sort Count -Descending | Select -First 4 Count,Name)
$rb  = (Test-Path 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\WindowsUpdate\Auto Update\RebootRequired') -or (Test-Path 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Component Based Servicing\RebootPending')
$su  = (Get-CimInstance Win32_StartupCommand | Measure-Object).Count
[pscustomobject]@{mp=$mp;det=$det;thr=$thr;fw=$fw;dk=$dk;ev_total=$ev.Count;ev=$evg;reboot=$rb;startup=$su} | ConvertTo-Json -Depth 5 -Compress
"""
CONHECIDOS = {"svchost.exe", "system", "lsass.exe", "wininit.exe", "services.exe", "spoolsv.exe", "ollama.exe", "ollama app.exe", "python.exe",
              "pythonw.exe", "msedgewebview2.exe", "chrome.exe", "discord.exe", "steam.exe", "steamwebhelper.exe", "code.exe", "msedge.exe",
              "spotify.exe", "nvcontainer.exe", "nvidia web helper.exe", "rundll32.exe", "searchindexer.exe", "mdnsresponder.exe",
              "onedrive.exe", "teams.exe", "slack.exe", "nvidia overlay.exe", "nvdisplay.container.exe", "wlanext.exe", "dashost.exe"}


def _lista(x):
    return [] if x is None else (x if isinstance(x, list) else [x])


def _data(v):
    if isinstance(v, str):
        m = re.search(r"/Date\((-?\d+)\)/", v)
        if m:
            return datetime.fromtimestamp(int(m.group(1)) / 1000)
    return None


def _quando(dt):
    if not dt:
        return "em data desconhecida"
    dias = (datetime.now().date() - dt.date()).days
    hora = f"{dt.hour} horas e {dt.minute} minutos"
    return f"hoje às {hora}" if dias == 0 else f"ontem às {hora}" if dias == 1 else f"há {dias} dias"


def _powershell(script, timeout=70):
    r = subprocess.run(["powershell", "-NoProfile", "-Command", script], capture_output=True, text=True, timeout=timeout,
                       creationflags=0x08000000, encoding="utf-8", errors="replace")
    return r.stdout.strip()


def _nome_ameaca(nome):
    """"Trojan:Win32/PShellDown.SB" -> "Trojan PShellDown" (fica falável)."""
    partes = re.split(r"[:/]", nome or "")
    tipo = partes[0] if len(partes) > 1 else ""
    resto = (partes[-1] or "").split(".")[0]
    return f"{tipo} {resto}".strip() or "uma ameaça"


def _nome_arquivo(recurso):
    m = re.search(r"([^\\/:;]+\.[A-Za-z0-9]{2,4})(?:;|$)", recurso or "")
    if m:
        return m.group(1)
    r = (recurso or "").lower()
    return "uma linha de comando" if "cmdline" in r else "um processo" if "process" in r else "um recurso do sistema"


def diagnosticar():
    """Roda as verificações e devolve {"itens": [(status, texto)], "fala": str}. status: ok | info | aviso | problema."""
    itens = []
    # ---------- recursos
    cpu = psutil.cpu_percent(interval=1.0)
    mem = psutil.virtual_memory()
    itens.append(("aviso" if mem.percent > 90 else "ok", f"Memória em {mem.percent:.0f} por cento ({mem.used / 2**30:.1f} de {mem.total / 2**30:.0f} gigas)."))
    itens.append(("aviso" if cpu > 90 else "ok", f"Processador em {cpu:.0f} por cento."))
    try:
        d = psutil.disk_usage(os.environ.get("SystemDrive", "C:") + "\\")
        livre = 100 - d.percent
        itens.append(("problema" if livre < 5 else "aviso" if livre < 12 else "ok", f"Disco do sistema com {livre:.0f} por cento livre ({d.free / 2**30:.0f} gigas)."))
    except Exception:
        pass
    up = (time.time() - psutil.boot_time()) / 86400
    itens.append(("aviso" if up > 14 else "ok", f"Computador ligado há {up:.0f} dias." if up >= 1 else "Computador ligado há menos de um dia."))
    try:
        s = subprocess.run(["nvidia-smi", "--query-gpu=temperature.gpu", "--format=csv,noheader,nounits"], capture_output=True, text=True,
                           timeout=4, creationflags=0x08000000).stdout.strip()
        t = float(s)
        itens.append(("aviso" if t > 85 else "ok", f"Placa de vídeo a {t:.0f} graus."))
    except Exception:
        pass
    top = []
    for p in psutil.process_iter(["name", "memory_info"]):
        try:
            top.append((p.info["memory_info"].rss, p.info["name"]))
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    top = sorted(top, reverse=True)[:3]
    itens.append(("info", "Os que mais usam memória: " + ", ".join(f"{n.replace('.exe', '')} com {b / 2**20:.0f} megas" for b, n in top) + "."))
    # ---------- segurança e saúde do Windows (PowerShell)
    try:
        dados = json.loads(_powershell(SCRIPT) or "{}")
    except Exception as e:
        dados = {}
        itens.append(("aviso", f"Não consegui ler parte dos dados do Windows ({type(e).__name__})."))
    mp = dados.get("mp") or {}
    if mp:
        if not mp.get("AntivirusEnabled") or not mp.get("RealTimeProtectionEnabled"):
            itens.append(("problema", "O antivírus ou a proteção em tempo real do Windows Defender está desligado."))
        else:
            itens.append(("ok", "Windows Defender ativo com proteção em tempo real."))
        if (mp.get("AntivirusSignatureAge") or 0) > 7:
            itens.append(("aviso", f"As definições do antivírus estão desatualizadas há {mp['AntivirusSignatureAge']} dias."))
        qa = mp.get("QuickScanAge")
        if isinstance(qa, int) and 14 < qa < 10000:
            itens.append(("aviso", f"A última verificação rápida do antivírus foi há {qa} dias."))
    ameacas = {a.get("ThreatID"): a for a in _lista(dados.get("thr"))}
    for det in _lista(dados.get("det"))[:3]:
        quando = _data(det.get("InitialDetectionTime"))
        if quando and (datetime.now() - quando).days <= 7:
            a = ameacas.get(det.get("ThreatID"), {})
            nome = _nome_ameaca(a.get("ThreatName", ""))
            arq = _nome_arquivo(det.get("Recurso"))
            resolvida = det.get("ActionSuccess") and not a.get("IsActive") and not a.get("DidThreatExecute")
            itens.append(("aviso" if resolvida else "problema",
                          f"O Defender detectou {quando and _quando(quando)} o item {nome} no arquivo {arq}. "
                          + ("Ele foi removido e não chegou a executar." if resolvida else "ATENÇÃO: a ação de remoção não foi confirmada.")))
    desligados = [f["Name"] for f in _lista(dados.get("fw")) if not f.get("Enabled")]
    itens.append(("problema", f"Firewall desligado no perfil {', '.join(desligados)}.") if desligados else ("ok", "Firewall do Windows ligado."))
    ruins = [f'{d["FriendlyName"]} ({d["HealthStatus"]})' for d in _lista(dados.get("dk")) if d.get("HealthStatus") != "Healthy"]
    itens.append(("problema", "Disco com problema de saúde: " + ", ".join(ruins) + ".") if ruins else ("ok", "Todos os discos informam estar saudáveis."))
    n_ev = dados.get("ev_total") or 0
    if n_ev:
        origem = ", ".join(e["Name"].replace("Microsoft-Windows-", "") for e in _lista(dados.get("ev"))[:2])
        itens.append(("aviso" if n_ev > 80 else "info", f"O registro do Windows tem {n_ev} erros nas últimas 24 horas, principalmente de {origem}."
                      + (" Isso costuma ser normal." if n_ev <= 80 else " É bastante.")))
    if dados.get("reboot"):
        itens.append(("aviso", "Há uma atualização esperando o computador reiniciar."))
    su = dados.get("startup") or 0
    if su:
        itens.append(("aviso" if su > 25 else "info", f"{su} programas iniciam junto com o Windows."))
    # ---------- rede: quem está escutando conexões de fora
    desconhecidos = set()
    try:
        for c in psutil.net_connections(kind="inet"):
            if c.status == "LISTEN" and c.laddr and c.laddr.ip in ("0.0.0.0", "::") and c.pid:
                try:
                    nome = psutil.Process(c.pid).name().lower()
                except psutil.Error:
                    continue
                if nome not in CONHECIDOS:
                    desconhecidos.add(nome)
    except Exception:
        pass
    if desconhecidos:
        itens.append(("aviso" if len(desconhecidos) > 6 else "info", "Programas aceitando conexões da rede: " + ", ".join(sorted(x.replace(".exe", "") for x in desconhecidos)[:6]) + "."))
    return {"itens": itens, "fala": _resumo(itens)}


def _resumo(itens):
    problemas = [t for s, t in itens if s == "problema"]
    avisos = [t for s, t in itens if s == "aviso"]
    n = len(itens)
    if not problemas and not avisos:
        base = f"Varredura concluída. Verifiquei {n} itens e não encontrei nada de errado."
    else:
        pontos = problemas + avisos
        base = (f"Varredura concluída. Verifiquei {n} itens e encontrei {len(pontos)} ponto{'s' if len(pontos) > 1 else ''} de atenção. "
                + " ".join(pontos[:4]))
    resumo = [t for s, t in itens if s == "ok" and ("Memória" in t or "Processador" in t or "Disco do sistema" in t)]
    extra = (" Fora isso, tudo normal: " + " ".join(resumo[:3])) if resumo and (problemas or avisos) else ""
    try:   # relatório completo em arquivo
        (config.DADOS / "diagnostico.txt").write_text(f"Varredura em {datetime.now():%d/%m/%Y %H:%M}\n" + "\n".join(f"[{s}] {t}" for s, t in itens), encoding="utf-8")
    except Exception:
        pass
    return base + extra


# ----------------------------------------------------------------- antivírus em segundo plano
def iniciar_antivirus(ao_concluir):
    """Inicia a verificação rápida do Windows Defender e chama `ao_concluir(texto)` quando terminar (pode levar vários minutos)."""
    def trabalho():
        inicio = datetime.now()
        try:
            saida = _powershell("Start-MpScan -ScanType QuickScan; "
                                "$d=@(Get-MpThreatDetection | Where-Object {$_.InitialDetectionTime -ge (Get-Date).AddMinutes(-60)} | "
                                "Select ThreatID,@{n='Recurso';e={($_.Resources -join ';')}}); "
                                "$t=@(Get-MpThreat | Select ThreatID,ThreatName); "
                                "[pscustomobject]@{det=$d;thr=$t} | ConvertTo-Json -Depth 4 -Compress", timeout=3600)
            dados = json.loads(saida or "{}")
            novas = _lista(dados.get("det"))
            nomes = {t.get("ThreatID"): t.get("ThreatName") for t in _lista(dados.get("thr"))}
            dur = (datetime.now() - inicio).total_seconds()
            tempo = f"{dur / 60:.0f} minutos" if dur >= 90 else f"{dur:.0f} segundos"
            if not novas:
                ao_concluir(f"A verificação rápida do antivírus terminou em {tempo}: nenhuma ameaça encontrada.")
            else:
                itens = "; ".join(f"{nomes.get(n.get('ThreatID'), 'ameaça')} em {_nome_arquivo(n.get('Recurso'))}" for n in novas[:3])
                ao_concluir(f"A verificação do antivírus terminou em {tempo} e o Defender tratou {len(novas)} item: {itens}.")
        except Exception as e:
            ao_concluir(f"A verificação do antivírus não terminou bem: {type(e).__name__}.")
    threading.Thread(target=trabalho, daemon=True, name="antivirus").start()


# ===================================================================================== varredura profunda
SCRIPT2 = r"""
$ErrorActionPreference='SilentlyContinue'
$tasks = @(Get-ScheduledTask | Where-Object { $_.TaskPath -notlike '\Microsoft\*' -and $_.State -ne 'Disabled' } | ForEach-Object { $a = $_.Actions | Select-Object -First 1; [pscustomobject]@{Nome=$_.TaskName; Exec=[string]$a.Execute; Args=[string]$a.Arguments} } | Select-Object -First 60)
$svc = @(Get-CimInstance Win32_Service | Where-Object { $_.StartMode -eq 'Auto' -and $_.PathName } | Select-Object Name,PathName -First 300)
$run = @(Get-CimInstance Win32_StartupCommand | Select-Object Name,Command,Location)
$mpp = Get-MpPreference
$excl = [pscustomobject]@{Caminhos=@($mpp.ExclusionPath);Processos=@($mpp.ExclusionProcess);Extensoes=@($mpp.ExclusionExtension)}
$hosts = @(Get-Content "$env:SystemRoot\System32\drivers\etc\hosts" | Where-Object { $_ -and $_ -notmatch '^\s*#' } | ForEach-Object { [string]$_ })
$pol = Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System'
$rdp = (Get-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Control\Terminal Server').fDenyTSConnections
$smb1 = (Get-SmbServerConfiguration).EnableSMB1Protocol
$guest = (Get-LocalUser -Name Guest).Enabled
$adm = @(Get-LocalGroupMember -SID 'S-1-5-32-544' | Select-Object -ExpandProperty Name)
$shares = @(Get-SmbShare | Where-Object { $_.Name -notmatch '^[A-Z]\$$|^IPC\$$|^ADMIN\$$' } | Select-Object Name,Path)
$bl = @(Get-BitLockerVolume | Select-Object MountPoint,ProtectionStatus)
$sb = $null; try { $sb = Confirm-SecureBootUEFI } catch {}
$auto = (Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon').AutoAdminLogon
[pscustomobject]@{tasks=$tasks;svc=$svc;run=$run;excl=$excl;hosts=$hosts;uac=$pol.EnableLUA;rdp=$rdp;smb1=$smb1;guest=$guest;adm=$adm;shares=$shares;bl=$bl;sb=$sb;auto=$auto} | ConvertTo-Json -Depth 5 -Compress
"""
GRAVAVEL = re.compile(r"\\(appdata|temp|downloads|users\\public)\\", re.I)
EXTS = {".exe", ".scr", ".bat", ".cmd", ".ps1", ".vbs", ".jar", ".msi"}
IGNORAR_NOVOS = {"node_modules", "site-packages", ".venv", ".venv_tts", "__pycache__", "npm", "pip", "code", "cache", "gradle", ".gradle", ".m2", "nuget",
                 "microsoft", "google", "discord", "spotify", "slack", "teams", "claude", "anthropic", "packages", "torch", "huggingface"}


def _exe_de(comando):
    m = re.search(r'"([^"]+\.(?:exe|dll|bat|cmd|ps1|vbs|msi))"', comando or "", re.I) or re.search(r"([A-Za-z]:\\[^ \"]+\.(?:exe|dll|bat|cmd|ps1|vbs|msi))", comando or "", re.I)
    return os.path.expandvars(m.group(1)) if m else None


def _assinaturas(caminhos):
    caminhos = [c for c in dict.fromkeys(caminhos) if c and os.path.exists(c)][:40]
    if not caminhos:
        return {}
    lista = ",".join("'" + c.replace("'", "''") + "'" for c in caminhos)
    try:
        saida = _powershell(f"Get-AuthenticodeSignature -FilePath {lista} | Select-Object Path,@{{n='S';e={{[string]$_.Status}}}} | ConvertTo-Json -Compress", timeout=90)
        return {i["Path"].lower(): i["S"] for i in _lista(json.loads(saida or "[]"))}
    except Exception:
        return {}


def _tamanho_pasta(caminho, tempo=8.0):
    total, fim = 0, time.time() + tempo
    pilha = [caminho]
    while pilha and time.time() < fim:
        d = pilha.pop()
        try:
            with os.scandir(d) as it:
                for e in it:
                    try:
                        if e.is_dir(follow_symlinks=False):
                            pilha.append(e.path)
                        else:
                            total += e.stat().st_size
                    except OSError:
                        pass
        except OSError:
            pass
    return total


def _arquivos_novos(dias=7, tempo=15.0):
    home = os.path.expanduser("~")
    raizes = [os.path.join(home, "Downloads"), os.path.join(home, "Desktop"), os.environ.get("TEMP", ""), os.path.join(home, "AppData", "Roaming"),
              r"C:\Users\Public", os.environ.get("ProgramData", "")]
    achados, fim, limite = [], time.time() + tempo, time.time() - dias * 86400
    for raiz in dict.fromkeys(r for r in raizes if r and os.path.isdir(r)):
        pilha = [(raiz, 0)]
        while pilha and time.time() < fim:
            d, prof = pilha.pop()
            try:
                with os.scandir(d) as it:
                    for e in it:
                        try:
                            if e.is_dir(follow_symlinks=False):
                                if prof < 3 and e.name.lower() not in IGNORAR_NOVOS and str(config.RAIZ).lower() not in e.path.lower():
                                    pilha.append((e.path, prof + 1))
                            elif os.path.splitext(e.name)[1].lower() in EXTS and e.stat().st_mtime > limite:
                                achados.append(e.path)
                        except OSError:
                            pass
            except OSError:
                pass
    return achados


def varredura_profunda(ao_aviso=None):
    """Tudo da varredura rápida + persistência, configurações de segurança, exclusões do antivírus, hosts, arquivos novos suspeitos e integridade."""
    from . import admin
    base = diagnosticar()
    itens = list(base["itens"])
    adm = admin.eh_admin()
    itens.append(("info" if adm else "aviso", "O Klaus está rodando como administrador e enxerga todas as áreas do sistema." if adm
                  else "O Klaus está sem privilégios de administrador, então algumas verificações ficam limitadas."))
    try:
        d = json.loads(_powershell(SCRIPT2, timeout=150) or "{}")
    except Exception as e:
        d = {}
        itens.append(("aviso", f"Parte das configurações do Windows não pôde ser lida ({type(e).__name__})."))
    # ---- persistência: tarefas agendadas, serviços e inicialização
    suspeitas = []
    for t in _lista(d.get("tasks")):
        if GRAVAVEL.search(t.get("Exec") or "") or re.search(r"-enc|encodedcommand|-w(indowstyle)?\s+hidden|downloadstring|frombase64", (t.get("Args") or ""), re.I):
            suspeitas.append(t.get("Nome"))
    itens.append(("aviso", "Tarefas agendadas que merecem conferência (rodam de pastas temporárias ou com comando oculto): " + ", ".join(suspeitas[:4]) + ".") if suspeitas
                 else ("info", f"{len(_lista(d.get('tasks')))} tarefas agendadas de terceiros, nenhuma com sinal suspeito."))
    svc_user = [s["Name"] for s in _lista(d.get("svc")) if GRAVAVEL.search(s.get("PathName") or "")]
    if svc_user:
        itens.append(("aviso", "Serviços automáticos rodando de pastas de usuário ou temporárias: " + ", ".join(svc_user[:4]) + "."))
    alvos = {}
    for r in _lista(d.get("run")):
        exe = _exe_de(r.get("Command"))
        if exe:
            alvos[exe] = r.get("Name")
    sig = _assinaturas(list(alvos))
    sem = [n for p, n in alvos.items() if sig.get(p.lower()) in ("NotSigned", "HashMismatch", "UnknownError")]
    if sem:
        itens.append(("aviso" if len(sem) > 3 else "info", "Programas de inicialização sem assinatura digital: " + ", ".join(sem[:5]) + "."))
    # ---- configurações de segurança
    ex = d.get("excl") or {}
    exclusoes = [x for x in _lista(ex.get("Caminhos")) + _lista(ex.get("Processos")) + _lista(ex.get("Extensoes")) if x]
    if exclusoes and "N/A" not in str(exclusoes):
        itens.append(("aviso", f"O Defender tem {len(exclusoes)} exclusões configuradas, locais que ele não verifica: " + ", ".join(os.path.basename(str(x).rstrip('\\')) or str(x) for x in exclusoes[:4]) + "."))
    elif not adm:
        itens.append(("info", "Sem administrador não consigo ver as exclusões do antivírus."))
    else:
        itens.append(("ok", "O antivírus não tem nenhuma exclusão configurada."))
    hosts = [h for h in (x if isinstance(x, str) else str(x.get("value", "")) for x in _lista(d.get("hosts"))) if h.strip() and "docker.internal" not in h and ".ts.net" not in h and not re.match(r"^\s*(127\.0\.0\.1|::1|0\.0\.0\.0)\s+localhost\s*$", h)]
    if hosts:
        itens.append(("aviso", f"O arquivo hosts tem {len(hosts)} entradas personalizadas, que redirecionam sites: " + "; ".join(re.sub(r"\s+", " ", h.strip()) for h in hosts[:2]) + "."))
    if d.get("uac") in (0, False):
        itens.append(("problema", "O controle de contas do usuário (UAC) está desligado."))
    if d.get("rdp") == 0:
        itens.append(("aviso", "A área de trabalho remota está habilitada."))
    if d.get("smb1"):
        itens.append(("problema", "O protocolo SMB1, antigo e inseguro, está ativo."))
    if d.get("guest"):
        itens.append(("aviso", "A conta de convidado está habilitada."))
    if str(d.get("auto")) == "1":
        itens.append(("aviso", "O login automático do Windows está ligado."))
    compart = [s["Name"] for s in _lista(d.get("shares"))]
    if compart:
        itens.append(("aviso", "Pastas compartilhadas na rede: " + ", ".join(compart[:4]) + "."))
    admins = _lista(d.get("adm"))
    if admins:
        itens.append(("aviso" if len(admins) > 3 else "info", f"{len(admins)} contas são administradoras locais."))
    if d.get("sb") is False:
        itens.append(("aviso", "A inicialização segura (Secure Boot) está desligada."))
    bl = [b for b in _lista(d.get("bl")) if b.get("ProtectionStatus") in (0, "Off")]
    if bl:
        itens.append(("info", "Discos sem criptografia BitLocker: " + ", ".join(b["MountPoint"] for b in bl[:3]) + "."))
    # ---- arquivos: executáveis e scripts novos em pastas graváveis
    novos = _arquivos_novos()
    if novos:
        assin = _assinaturas([p for p in novos if os.path.splitext(p)[1].lower() in (".exe", ".dll", ".msi", ".scr")][:30])
        suspeitos = [p for p in novos if assin.get(p.lower()) in ("NotSigned", "HashMismatch") and re.search(r"\\(temp|users\\public|roaming)\\", p, re.I)]
        if suspeitos:
            itens.append(("aviso", f"{len(novos)} executáveis ou scripts novos nos últimos 7 dias; sem assinatura digital em pastas temporárias: "
                          + ", ".join(os.path.basename(p) for p in suspeitos[:4]) + ". Confira se foi você quem instalou."))
        else:
            itens.append(("info", f"{len(novos)} executáveis ou scripts novos nos últimos 7 dias em pastas de usuário, nenhum suspeito."))
    # ---- espaço usado por lixo
    try:
        tmp = _tamanho_pasta(os.environ.get("TEMP", ""), 6)
        dl = _tamanho_pasta(os.path.join(os.path.expanduser("~"), "Downloads"), 6)
        itens.append(("aviso" if tmp > 5 * 2**30 else "info", f"A pasta temporária ocupa {tmp / 2**30:.1f} gigas e a de downloads {dl / 2**30:.1f}."))
    except Exception:
        pass
    # ---- integridade da imagem do Windows (só com administrador)
    if adm:
        try:
            r = subprocess.run(["Dism", "/Online", "/Cleanup-Image", "/CheckHealth"], capture_output=True, text=True, timeout=120, creationflags=0x08000000, errors="replace")
            ok = re.search(r"no component store corruption|nenhuma corrup", r.stdout, re.I)
            itens.append(("ok", "A imagem do Windows está íntegra.") if ok else ("aviso", "O Windows reportou possível corrupção nos componentes do sistema; vale rodar o reparo."))
        except Exception:
            pass
    fala = _resumo_profundo(itens)
    return {"itens": itens, "fala": fala}


def _resumo_profundo(itens):
    problemas = [t for s, t in itens if s == "problema"]
    avisos = [t for s, t in itens if s == "aviso"]
    try:
        (config.DADOS / "varredura_profunda.txt").write_text(f"Varredura profunda em {datetime.now():%d/%m/%Y %H:%M}\n" + "\n".join(f"[{s}] {t}" for s, t in itens), encoding="utf-8")
    except Exception:
        pass
    n = len(itens)
    if not problemas and not avisos:
        return f"Varredura profunda concluída. Conferi {n} itens, entre arquivos, configurações de segurança e inicialização, e não encontrei nada de errado."
    pontos = problemas + avisos
    return (f"Varredura profunda concluída. Conferi {n} itens e encontrei {len(pontos)} ponto{'s' if len(pontos) > 1 else ''} de atenção"
            + (f", {len(problemas)} deles sério{'s' if len(problemas) > 1 else ''}" if problemas else "") + ". " + " ".join(pontos[:5])
            + (f" Tem mais {len(pontos) - 5} no relatório completo, salvo na pasta dados." if len(pontos) > 5 else " O relatório completo está na pasta dados."))


def iniciar_integridade(ao_concluir):
    """Verifica os arquivos do sistema (sfc /verifyonly) em segundo plano; exige administrador. Pode levar vários minutos."""
    from . import admin
    if not admin.eh_admin():
        ao_concluir("Para verificar os arquivos do sistema o Klaus precisa estar rodando como administrador.")
        return

    def trabalho():
        inicio = time.time()
        try:
            r = subprocess.run(["sfc", "/verifyonly"], capture_output=True, timeout=3600, creationflags=0x08000000)
            texto = (r.stdout or b"").decode("utf-16-le", errors="ignore") if b"\x00" in (r.stdout or b"")[:20] else (r.stdout or b"").decode("cp850", errors="ignore")
            texto = re.sub(r"\s+", " ", texto)
            tempo = f"{(time.time() - inicio) / 60:.0f} minutos"
            if re.search(r"n[aã]o encontrou|did not find|no integrity", texto, re.I):
                ao_concluir(f"A verificação dos arquivos do sistema terminou em {tempo}: nenhuma violação de integridade encontrada.")
            elif re.search(r"encontrou viola|found integrity", texto, re.I):
                ao_concluir(f"A verificação dos arquivos do sistema terminou em {tempo} e encontrou arquivos danificados. Posso rodar o reparo se você mandar, com a sua confirmação.")
            else:
                ao_concluir(f"A verificação dos arquivos do sistema terminou em {tempo}, mas não consegui interpretar o resultado.")
        except Exception as e:
            ao_concluir(f"A verificação dos arquivos do sistema não terminou bem: {type(e).__name__}.")
    threading.Thread(target=trabalho, daemon=True, name="sfc").start()
