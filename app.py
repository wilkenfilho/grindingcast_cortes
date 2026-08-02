"""
Grindingcast - Gerador de Cortes de Podcast
--------------------------------------------
App em Streamlit que:
1. Recebe upload temporário de um episódio em MP3, WAV ou M4A
2. Recebe o título do jogo / tema do episódio
3. Transcreve o áudio com timestamps usando Whisper LOCAL (faster-whisper),
   rodando no seu computador — sem gastar tokens/créditos da Groq
4. Usa um modelo de linguagem da Groq (gratuito) para sugerir cortes de
   2 a 3 minutos, com título e justificativa para cada corte
5. Permite exportar a lista de cortes sugeridos em Excel (.xlsx)

Como rodar:
    pip install -r requirements.txt
    streamlit run app.py

IMPORTANTE — chave da API da Groq via st.secrets:
Crie o arquivo `.streamlit/secrets.toml` (veja o exemplo
`.streamlit/secrets.toml.example` incluído) com o conteúdo:

    GROQ_API_KEY = "sua_chave_aqui"

A chave NUNCA fica escrita no código-fonte. Se você já expôs uma chave em
algum lugar (chat, print, repositório público etc.), revogue-a em
https://console.groq.com/keys e gere outra.
"""

import io
import json
import os
import tempfile
import time
from dataclasses import dataclass

import pandas as pd
import streamlit as st

from faster_whisper import WhisperModel
from groq import Groq

# --------------------------------------------------------------------------
# Configurações gerais
# --------------------------------------------------------------------------

st.set_page_config(page_title="Grindingcast - Gerador de Cortes", page_icon="🎙️", layout="wide")

LLM_MODEL = "llama-3.3-70b-versatile"  # modelo de texto (Groq) para sugerir os cortes

TAMANHOS_WHISPER = ["tiny", "base", "small", "medium", "large-v3"]


@dataclass
class Corte:
    titulo: str
    inicio: str
    fim: str
    duracao_segundos: int
    motivo: str


# --------------------------------------------------------------------------
# Funções auxiliares
# --------------------------------------------------------------------------

def segundos_para_mmss(segundos: float) -> str:
    segundos = int(round(segundos))
    h, resto = divmod(segundos, 3600)
    m, s = divmod(resto, 60)
    if h:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def formatar_duracao(segundos: float) -> str:
    segundos = max(int(round(segundos)), 0)
    h, resto = divmod(segundos, 3600)
    m, s = divmod(resto, 60)
    if h:
        return f"{h}h{m:02d}m{s:02d}s"
    if m:
        return f"{m}m{s:02d}s"
    return f"{s}s"


@st.cache_resource(show_spinner=False)
def carregar_modelo_whisper(tamanho_modelo: str, device: str, compute_type: str) -> WhisperModel:
    """
    Carrega (e mantém em cache) o modelo Whisper local. Na primeira
    execução, o faster-whisper baixa os pesos do modelo automaticamente
    (é necessário internet só nessa primeira vez); depois fica em cache
    local no disco.
    """
    return WhisperModel(tamanho_modelo, device=device, compute_type=compute_type)


def transcrever_audio_local(
    caminho_audio: str,
    tamanho_modelo: str,
    device: str,
    compute_type: str,
    progress_bar,
    status_placeholder,
) -> list:
    """
    Transcreve o áudio localmente com faster-whisper (sem usar a API da
    Groq) e retorna uma lista de segmentos no formato:
        {"start": float, "end": float, "text": str}
    Atualiza barra de progresso, porcentagem e ETA em tempo real.
    """
    status_placeholder.info("Carregando modelo Whisper local (pode demorar na primeira vez)...")
    modelo = carregar_modelo_whisper(tamanho_modelo, device, compute_type)

    segmentos_generator, info = modelo.transcribe(
        caminho_audio,
        language="pt",
        vad_filter=True,
        beam_size=5,
    )
    duracao_total = info.duration or 0.0

    segmentos = []
    inicio_processamento = time.time()

    for seg in segmentos_generator:
        segmentos.append({
            "start": seg.start,
            "end": seg.end,
            "text": seg.text.strip(),
        })

        fracao = min(seg.end / duracao_total, 1.0) if duracao_total > 0 else 0.0
        decorrido = time.time() - inicio_processamento
        eta_segundos = (decorrido / fracao - decorrido) if fracao > 0.01 else None

        progress_bar.progress(fracao)
        texto_eta = f"ETA: {formatar_duracao(eta_segundos)}" if eta_segundos is not None else "ETA: calculando..."
        status_placeholder.info(
            f"Transcrevendo localmente... {fracao * 100:.1f}% "
            f"({formatar_duracao(seg.end)} / {formatar_duracao(duracao_total)}) — {texto_eta}"
        )

    progress_bar.progress(1.0)
    status_placeholder.success(f"Transcrição local concluída em {formatar_duracao(time.time() - inicio_processamento)}.")
    return segmentos


def montar_transcricao_formatada(segmentos: list) -> str:
    linhas = []
    for seg in segmentos:
        inicio = segundos_para_mmss(seg["start"])
        fim = segundos_para_mmss(seg["end"])
        linhas.append(f"[{inicio} - {fim}] {seg['text']}")
    return "\n".join(linhas)


def gerar_sugestoes_de_corte(client: Groq, transcricao_formatada: str, tema: str) -> list:
    """
    Envia a transcrição com timestamps para o modelo de linguagem da Groq
    e pede sugestões de cortes de 2 a 3 minutos, com título e motivo.
    Retorna uma lista de dicts.
    """
    prompt_sistema = (
        "Você é um editor de vídeo especialista em criar cortes curtos e "
        "virais a partir de podcasts de games para redes sociais (Reels, "
        "Shorts, TikTok). Você recebe a transcrição completa de um episódio "
        "do podcast Grindingcast, com timestamps no formato [mm:ss - mm:ss], "
        "e deve sugerir os melhores trechos para cortar."
    )

    prompt_usuario = f"""
Tema/jogo do episódio: {tema or "não informado"}

Transcrição completa com timestamps:
---
{transcricao_formatada}
---

Analise a transcrição acima e sugira de 5 a 12 cortes (clipes) para redes
sociais. Regras obrigatórias:
- Cada corte deve durar entre 2 e 3 minutos (120 a 180 segundos).
- Use SOMENTE timestamps que existem na transcrição.
- Cada corte precisa de um título curto e chamativo (estilo redes sociais).
- Cada corte precisa de uma justificativa objetiva: por que esse trecho
  prende atenção, gera engajamento ou é relevante para o tema informado.
- Não invente falas nem timestamps fora do intervalo do episódio.

Responda APENAS com um JSON válido (sem markdown, sem texto extra), no
formato exato abaixo:

{{
  "cortes": [
    {{
      "titulo": "string",
      "inicio": "mm:ss",
      "fim": "mm:ss",
      "motivo": "string"
    }}
  ]
}}
"""

    resposta = client.chat.completions.create(
        model=LLM_MODEL,
        messages=[
            {"role": "system", "content": prompt_sistema},
            {"role": "user", "content": prompt_usuario},
        ],
        temperature=0.4,
        response_format={"type": "json_object"},
    )

    conteudo = resposta.choices[0].message.content
    dados = json.loads(conteudo)
    return dados.get("cortes", [])


def mmss_para_segundos(valor: str) -> int:
    partes = [int(p) for p in valor.split(":")]
    if len(partes) == 2:
        m, s = partes
        return m * 60 + s
    if len(partes) == 3:
        h, m, s = partes
        return h * 3600 + m * 60 + s
    return 0


def gerar_excel(cortes: list) -> bytes:
    linhas = []
    for c in cortes:
        inicio_s = mmss_para_segundos(c["inicio"])
        fim_s = mmss_para_segundos(c["fim"])
        linhas.append({
            "Título": c["titulo"],
            "Início": c["inicio"],
            "Fim": c["fim"],
            "Duração (s)": max(fim_s - inicio_s, 0),
            "Motivo do corte": c["motivo"],
        })

    df = pd.DataFrame(linhas)
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Cortes Sugeridos")
        ws = writer.sheets["Cortes Sugeridos"]
        larguras = {"A": 40, "B": 10, "C": 10, "D": 14, "E": 60}
        for col, largura in larguras.items():
            ws.column_dimensions[col].width = largura
    buffer.seek(0)
    return buffer.getvalue()


# --------------------------------------------------------------------------
# Interface Streamlit
# --------------------------------------------------------------------------

st.title("🎙️ Grindingcast — Gerador de Cortes")
st.caption("Transcrição 100% local (Whisper) + sugestões de cortes de 2 a 3 minutos usando IA da Groq")


def obter_api_key_groq() -> str:
    """Lê a chave da Groq de st.secrets (recomendado) ou de variável de ambiente."""
    try:
        if "GROQ_API_KEY" in st.secrets:
            return st.secrets["GROQ_API_KEY"]
    except Exception:
        pass
    return os.environ.get("GROQ_API_KEY", "")


with st.sidebar:
    st.header("Configuração")

    api_key = obter_api_key_groq()
    if api_key:
        st.success("Chave da Groq carregada de st.secrets ✅")
    else:
        st.error(
            "Nenhuma chave da Groq encontrada em st.secrets.\n\n"
            "Crie o arquivo `.streamlit/secrets.toml` com:\n\n"
            '`GROQ_API_KEY = "sua_chave_aqui"`\n\n'
            "(veja `.streamlit/secrets.toml.example`). Se estiver no "
            "Streamlit Community Cloud, cadastre a chave em "
            "Settings → Secrets do seu app."
        )

    st.markdown("---")
    st.subheader("Whisper local")
    tamanho_modelo = st.selectbox(
        "Tamanho do modelo",
        TAMANHOS_WHISPER,
        index=TAMANHOS_WHISPER.index("small"),
        help=(
            "tiny/base = mais rápido, menos preciso. "
            "medium/large-v3 = mais preciso, bem mais lento sem GPU."
        ),
    )
    device = st.selectbox("Dispositivo", ["cpu", "cuda"], index=0, help="Use 'cuda' se tiver GPU NVIDIA com CUDA configurado.")
    compute_type = st.selectbox(
        "Precisão (compute_type)",
        ["int8", "int8_float16", "float16", "float32"],
        index=0,
        help="int8 é o mais rápido/leve para CPU. float16 é recomendado para GPU.",
    )

    st.markdown("---")
    st.markdown(
        "⚠️ **Nunca compartilhe sua chave de API em chats, prints ou "
        "repositórios públicos.** Se isso já aconteceu, revogue a chave "
        "antiga em console.groq.com/keys e crie uma nova."
    )

if "segmentos" not in st.session_state:
    st.session_state.segmentos = None
if "cortes" not in st.session_state:
    st.session_state.cortes = None

col1, col2 = st.columns(2)
with col1:
    arquivo_audio = st.file_uploader("Upload do episódio (MP3, WAV ou M4A)", type=["mp3", "wav", "m4a"])
with col2:
    tema = st.text_input("Título do jogo / tema do episódio", placeholder="Ex: Elden Ring, Baldur's Gate 3...")

if arquivo_audio is not None:
    tamanho_mb = arquivo_audio.size / (1024 * 1024)
    st.caption(f"Arquivo: {arquivo_audio.name} — {tamanho_mb:.1f}MB")
    if tamanho_mb > 300 and tamanho_modelo in ("medium", "large-v3") and device == "cpu":
        st.warning(
            f"Esse arquivo é grande ({tamanho_mb:.0f}MB, provavelmente um episódio longo). "
            "Rodando em CPU com o modelo "
            f"'{tamanho_modelo}', a transcrição pode levar muitas horas. "
            "Para episódios longos, considere usar 'tiny', 'base' ou 'small' "
            "na barra lateral (ou 'device = cuda' se tiver GPU)."
        )

processar = st.button("🚀 Transcrever e gerar cortes", type="primary", disabled=not (arquivo_audio and api_key))

if not arquivo_audio:
    st.info("Envie um arquivo de áudio (MP3, WAV ou M4A) para começar.")
elif not api_key:
    st.info("Configure sua chave da Groq em st.secrets para continuar (veja a barra lateral).")

if processar and arquivo_audio and api_key:
    extensao = os.path.splitext(arquivo_audio.name)[1].lower() or ".mp3"
    with tempfile.NamedTemporaryFile(delete=False, suffix=extensao) as tmp:
        tmp.write(arquivo_audio.read())
        caminho_temp = tmp.name

    progress_bar = st.progress(0.0)
    status = st.empty()
    try:
        segmentos = transcrever_audio_local(
            caminho_temp, tamanho_modelo, device, compute_type, progress_bar, status
        )
        st.session_state.segmentos = segmentos

        if not segmentos:
            st.warning("Nenhum trecho de fala foi identificado no áudio.")
        else:
            transcricao_formatada = montar_transcricao_formatada(segmentos)

            client = Groq(api_key=api_key)
            with st.spinner("Gerando sugestões de cortes com IA da Groq..."):
                cortes = gerar_sugestoes_de_corte(client, transcricao_formatada, tema)
            st.session_state.cortes = cortes
            st.success(f"{len(cortes)} cortes sugeridos!")

    except Exception as e:
        st.error(f"Ocorreu um erro: {e}")
    finally:
        try:
            os.remove(caminho_temp)
        except OSError:
            pass

# --------------------------------------------------------------------------
# Resultados
# --------------------------------------------------------------------------

if st.session_state.cortes:
    st.subheader("✂️ Cortes sugeridos")
    df_cortes = pd.DataFrame(st.session_state.cortes)
    df_cortes = df_cortes.rename(columns={
        "titulo": "Título", "inicio": "Início", "fim": "Fim", "motivo": "Motivo do corte"
    })
    st.dataframe(df_cortes, use_container_width=True)

    excel_bytes = gerar_excel(st.session_state.cortes)
    st.download_button(
        "📥 Baixar cortes em Excel",
        data=excel_bytes,
        file_name=f"cortes_{(tema or 'episodio').strip().replace(' ', '_').lower()}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )

if st.session_state.segmentos:
    with st.expander("Ver transcrição completa com timestamps"):
        st.text(montar_transcricao_formatada(st.session_state.segmentos))
