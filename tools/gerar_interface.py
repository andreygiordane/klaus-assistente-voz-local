"""Gera interface/klaus_hud.html a partir do HUD original, ligando-o ao Klaus local (ponte.py).
Uso: python tools/gerar_interface.py ORIGINAL.html interface/klaus_hud.html"""
import sys
from pathlib import Path

ORIG, SAIDA = Path(sys.argv[1]), Path(sys.argv[2])
h = ORIG.read_text(encoding="utf-8")


def troca(velho, novo):
    global h
    assert h.count(velho) == 1, f"trecho encontrado {h.count(velho)}x: {velho[:70]!r}"
    h = h.replace(velho, novo)


# 1. nuvem fora: o cérebro agora é local
i = h.index('      const apiKey = "";')
j = h.index("      const SYSTEM_INSTRUCTION")
h = h[:i] + '      const WS_URL = "ws://127.0.0.1:8765";   // Klaus local (assistente/ponte.py)\n\n' + h[j:]
troca('state.chatHistory = [];\n          dom.chatTabPanel',
      'state.chatHistory = [];\n          ponteEnviar({ tipo: "limpar" });\n          dom.chatTabPanel')

# 2. cliente da ponte (antes dos efeitos sonoros)
PONTE_JS = r'''
      /* ----------------------------------------------------------------------
         PONTE COM O KLAUS LOCAL (WebSocket): estados, conversa, telemetria real
         ---------------------------------------------------------------------- */
      const ponte = { ws: null, ok: false };
      const ESTADOS = ["IDLE", "LISTENING", "PROCESSING", "SPEAKING"];

      function ponteEnviar(obj) {
        if (ponte.ok && ponte.ws && ponte.ws.readyState === 1) {
          ponte.ws.send(JSON.stringify(obj));
          return true;
        }
        return false;
      }

      function marcarConexao(ok) {
        ponte.ok = ok;
        const badge = document.getElementById("onlineBadge");
        if (badge) {
          badge.lastChild.textContent = ok ? " KLAUS LOCAL" : " OFFLINE";
          badge.classList.toggle("border-emerald-400/40", ok);
          badge.classList.toggle("text-emerald-300", ok);
          badge.classList.toggle("border-red-400/50", !ok);
          badge.classList.toggle("text-red-300", !ok);
        }
        dom.terminalStatus.textContent = ok ? "LINK KLAUS LOCAL: CONECTADO" : "LINK KLAUS LOCAL: SEM CONEXÃO";
      }

      function aplicarTelemetria(t) {
        const set = (val, bar, v) => {
          if (v === null || v === undefined) return;
          const n = Math.round(v);
          val.textContent = `${n}%`;
          bar.style.width = `${clamp(n, 0, 100)}%`;
        };
        set(dom.valCpu, dom.barCpu, t.cpu);
        set(dom.valGpu, dom.barGpu, t.gpu);
        set(dom.valRam, dom.barRam, t.ram);
        set(dom.valNeural, dom.barNeural, t.vram);
        if (t.temp !== null && t.temp !== undefined) {
          dom.valTemp.textContent = `${Math.round(t.temp)}°C`;
          dom.barTemp.style.width = `${clamp(Math.round(t.temp), 10, 100)}%`;
        }
        dom.valPing.textContent = "LOCAL";
        dom.valBattery.textContent = (t.bateria === null || t.bateria === undefined) ? "AC" : `${t.carregando ? "⚡" : ""}${t.bateria}%`;
      }

      function aplicarInfo(info) {
        let chip = document.getElementById("chipPessoa");
        if (!chip) {
          const badge = document.getElementById("onlineBadge");
          chip = document.createElement("span");
          chip.id = "chipPessoa";
          chip.className = "inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-[10px] sm:text-xs font-mono-tech border border-[var(--hud-border)] bg-black/30 hud-text-primary";
          if (badge && badge.parentNode) badge.parentNode.appendChild(chip);
        }
        chip.textContent = info.pessoa ? `VOZ: ${info.pessoa.toUpperCase()}` : "VOZ: NÃO IDENTIFICADA";
        chip.title = `Voz do Klaus: ${info.voz || "?"}${info.cidade ? " • " + info.cidade : ""}`;
      }

      function ponteMensagem(m) {
        switch (m.tipo) {
          case "estado":
            if (ESTADOS.includes(m.valor)) setCoreState(m.valor);
            break;
          case "usuario":
            switchRightTab("chat");
            appendChatMessage("user", `${m.pessoa ? "**" + m.pessoa + ":** " : ""}${m.texto}`);
            addSystemLog(`Voz reconhecida${m.pessoa ? " (" + m.pessoa + ", " + m.similaridade + ")" : ""}: "${m.texto}"`, "OK");
            break;
          case "klaus_parcial":
            addSystemLog(`Klaus: ${m.texto}`, "INFO");
            break;
          case "klaus":
            switchRightTab("chat");
            appendChatMessage("klaus", m.texto);
            playSfx("receive");
            break;
          case "telemetria":
            aplicarTelemetria(m);
            break;
          case "info":
            aplicarInfo(m);
            break;
        }
      }

      function ponteConectar() {
        try {
          ponte.ws = new WebSocket(WS_URL);
        } catch {
          setTimeout(ponteConectar, 3000);
          return;
        }
        ponte.ws.onopen = () => {
          marcarConexao(true);
          addSystemLog("Conectado ao núcleo local do KLAUS.", "OK");
        };
        ponte.ws.onmessage = (ev) => {
          try { ponteMensagem(JSON.parse(ev.data)); } catch { /* mensagem inválida: ignora */ }
        };
        ponte.ws.onclose = () => {
          if (ponte.ok) addSystemLog("Conexão com o núcleo local perdida. Reconectando...", "WARN");
          marcarConexao(false);
          setTimeout(ponteConectar, 2500);
        };
        ponte.ws.onerror = () => { try { ponte.ws.close(); } catch { /* nada */ } };
      }
'''
troca("      /* ----------------------------------------------------------------------\n         SISTEMA DE EFEITOS SONOROS SINTETIZADOS",
      PONTE_JS + "\n      /* ----------------------------------------------------------------------\n         SISTEMA DE EFEITOS SONOROS SINTETIZADOS")

# 3. badge ONLINE vira status real da conexão
troca('<span class="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-[10px] sm:text-xs font-mono-tech border border-emerald-400/40 bg-emerald-500/10 text-emerald-300">',
      '<span id="onlineBadge" class="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-[10px] sm:text-xs font-mono-tech border border-red-400/50 bg-emerald-500/10 text-red-300">')
troca("              ONLINE\n            </span>", "               OFFLINE\n            </span>")
troca("PROCESSANDO VIA GEMINI API...", "PROCESSANDO NO CÉREBRO LOCAL...")

# 4. parar de falar, telemetria real, microfone
troca('''      function stopSpeaking() {
        if ("speechSynthesis" in window) {''', '''      function stopSpeaking() {
        ponteEnviar({ tipo: "parar" });
        if ("speechSynthesis" in window) {''')
troca('''      function updateTelemetry() {
        const boost''', '''      function updateTelemetry() {
        if (ponte.ok) return;   // com o Klaus conectado, os números reais chegam pela ponte
        const boost''')
troca('''      function toggleVoiceRecognition() {
        if (!state.recognition) {''', '''      function toggleVoiceRecognition() {
        if (ponteEnviar({ tipo: "ouvir" })) {      // o Klaus já escuta pelo microfone do PC
          playSfx("activate");
          setCoreState("LISTENING");
          showToast("KLAUS ouvindo: pode falar sem dizer o nome.");
          return;
        }
        if (!state.recognition) {''')

# 5. mensagem do usuário: aparece no chat, limpa o campo e vai ao Klaus local (sem Gemini)
i = h.index("      async function callGeminiWithBackoff")
j = h.index("      /* ----------------------------------------------------------------------\n         PROTOCOLOS ESPECIAIS")
NOVO = r'''      async function handleUserMessage(rawText) {
        const text = rawText.trim();
        if (!text || state.coreState === "PROCESSING") return;

        stopSpeaking();
        playSfx("click");
        switchRightTab("chat");
        appendChatMessage("user", text);
        dom.commandInput.value = "";

        if (!ponteEnviar({ tipo: "texto", texto: text })) {
          addSystemLog("Núcleo local do KLAUS desconectado. Inicie o Klaus (iniciar_klaus.bat).", "ERR");
          appendChatMessage("klaus", "Estou sem conexão com o meu núcleo local, **Senhor**. Inicie o `iniciar_klaus.bat` e eu volto a responder.");
          showToast("KLAUS local desconectado.");
          return;
        }
        setCoreState("PROCESSING");
        addSystemLog("Comando enviado ao núcleo local.", "INFO");
      }

'''
h = h[:i] + NOVO + h[j:]

# 6. voz ligada/desligada vale também para o Klaus local
troca('''          if (!state.ttsEnabled) stopSpeaking();
          playSfx("click");''', '''          ponteEnviar({ tipo: "mudo", valor: !state.ttsEnabled });
          if (!state.ttsEnabled) stopSpeaking();
          playSfx("click");''')

# 7. iniciar conexão
troca("        loadTasks();\n        initSpeechRecognition();", "        loadTasks();\n        ponteConectar();\n        initSpeechRecognition();")

SAIDA.write_text(h, encoding="utf-8")
print("ok", len(h), "bytes")
