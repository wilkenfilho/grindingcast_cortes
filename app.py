"""
Grindingcast - Gerador de Cortes de Podcast
--------------------------------------------
App em Streamlit que:
1. Recebe upload temporário de um episódio em MP3, WAV ou M4A
2. Recebe o título do jogo / tema do episódio
3. Transcreve o áudio com timestamps usando a API Whisper da Groq
   (whisper-large-v3-turbo), dividindo o áudio em partes e pausando
   automaticamente para respeitar o limite gratuito da Groq
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

SOBRE O LIMITE GRATUITO DA GROQ:
O plano free da Groq para transcrição (Whisper) não cobra dinheiro, mas
tem um teto de uso: aproximadamente 7200 segundos (2 horas) de áudio por
hora corrida, e 2000 requisições por dia. Para episódios muito longos
(ex.: ~10h), este app divide o áudio em partes e PAUSA automaticamente
quando necessário para não estourar esse teto — ou seja, o processo pode
levar bem mais tempo em relógio de parede do que a duração do episódio,
mas sem custar nada. Dá para desativar essa pausa automática na barra
lateral caso você tenha um plano pago da Groq com limites maiores.
"""

import io
import json
import math
import os
import tempfile
import time
from dataclasses import dataclass
import audioop  # tenta forçar a importação
from pydub import AudioSegment

import pandas as pd
import streamlit as st

from pydub import AudioSegment
from groq import Groq

# --------------------------------------------------------------------------
# Configurações gerais
# --------------------------------------------------------------------------

st.set_page_config(page_title="Grindingcast - Gerador de Cortes", page_icon="🎙️", layout="wide")

TRANSCRIPTION_MODEL = "whisper-large-v3-turbo"  # transcrição (Groq)
LLM_MODEL = "llama-3.3-70b-versatile"           # sugestão de cortes (Groq)

# Duração de cada pedaço de áudio enviado à Groq (segundos).
CHUNK_DURATION_SECONDS = 600  # 10 minutos por parte

# Limite gratuito aproximado da Groq para transcrição: ~7200s (2h) de
# áudio processado por hora corrida. Usamos uma margem de segurança.
LIMITE_SEGUNDOS_POR_HORA = 7200
MARGEM_SEGURANCA = 0.9  # usa até 90% do teto, para folga


@dataclass
class Corte:
    titulo: str
    inicio: str
    fim: str
    duracao_segundos: int
    motivo: str


# --------------------------------------------------------------------------
# Funções auxiliares - formatação
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


def mmss_para_segundos(valor: str) -> int:
    partes = [int(p) for p in valor.split(":")]
    if len(partes) == 2:
        m, s = partes
        return m * 60 + s
    if len(partes) == 3:
        h, m, s = partes
        return h * 3600 + m * 60 + s
    return 0


# --------------------------------------------------------------------------
# Divisão do áudio em partes
# --------------------------------------------------------------------------

def dividir_audio_em_chunks(caminho_audio: str, chunk_duration_seconds: int = CHUNK_DURATION_SECONDS):
    """
    Divide o áudio em partes de tamanho fixo (chunk_duration_seconds) e as
    exporta como MP3 comprimido (64kbps), para ficar bem abaixo do limite
    de tamanho de arquivo da API da Groq. Retorna:
        (lista_de_chunks, duracao_total_segundos)
    onde cada chunk é (caminho_arquivo, offset_segundos, duracao_segundos)
    """
    audio = AudioSegment.from_file(caminho_audio)
    duracao_total_ms = len(audio)
    chunk_ms = chunk_duration_seconds * 1000
    n_chunks = max(math.ceil(duracao_total_ms / chunk_ms), 1)

    tmpdir = tempfile.mkdtemp(prefix="grindingcast_chunks_")
    chunks = []
    for i in range(n_chunks):
        inicio_ms = i * chunk_ms
        fim_ms = min((i + 1) * chunk_ms, duracao_total_ms)
        pedaco = audio[inicio_ms:fim_ms]
        caminho_pedaco = os.path.join(tmpdir, f"chunk_{i:04d}.mp3")
        pedaco.export(caminho_pedaco, format="mp3", bitrate="64k")
        chunks.append((caminho_pedaco, inicio_ms / 1000.0, (fim_ms - inicio_ms) / 1000.0))

    return chunks, duracao_total_ms / 1000.0


# --------------------------------------------------------------------------
# Controle de cota gratuita da Groq
# --------------------------------------------------------------------------

def aguardar_cota_disponivel(duracao_chunk: float, historico_envios: list, status_placeholder, respeitar_limite: bool):
    """
    Bloqueia (com sleep) até que haja cota suficiente, na janela móvel da
    última hora, para enviar mais 'duracao_chunk' segundos de áudio à API
    de transcrição da Groq. historico_envios é uma lista de
    (timestamp_envio, duracao_segundos) mutável, atualizada aqui.
    """
    if not respeitar_limite:
        return

    limite = LIMITE_SEGUNDOS_POR_HORA * MARGEM_SEGURANCA

    while True:
        agora = time.time()
        # descarta envios com mais de 1h (janela móvel)
        historico_envios[:] = [(t, d) for (t, d) in historico_envios if agora - t < 3600]
        usado = sum(d for _, d in historico_envios)

        if usado + duracao_chunk <= limite or not historico_envios:
            return

        envio_mais_antigo = historico_envios[0][0]
        espera = max(3600 - (agora - envio_mais_antigo) + 2, 1)
        status_placeholder.warning(
            f"⏸️ Cota gratuita da Groq quase no limite. Pausando por "
            f"{formatar_duracao(espera)} antes de continuar (isso é automático, "
            f"não precisa fazer nada)."
        )
        time.sleep(min(espera, 20))


# --------------------------------------------------------------------------
# Transcrição via API da Groq
# --------------------------------------------------------------------------

def transcrever_chunk_groq(client: Groq, caminho_chunk: str) -> list:
    with open(caminho_chunk, "rb") as f:
        resposta = client.audio.transcriptions.create(
            file=(os.path.basename(caminho_chunk), f.read()),
            model=TRANSCRIPTION_MODEL,
            response_format="verbose_json",
            timestamp_granularities=["segment"],
            language="pt",
        )
    return getattr(resposta, "segments", None) or resposta.get("segments", [])


def transcrever_audio_groq(
    client: Groq,
    caminho_audio: str,
    progress_bar,
    status_placeholder,
    respeitar_limite: bool,
) -> list:
    """
    Transcreve o áudio via API da Groq, dividindo em partes e pausando
    automaticamente para respeitar o limite gratuito. Retorna uma lista
    de segmentos: {"start": float, "end": float, "text": str}
    """
    status_placeholder.info("Preparando áudio (dividindo em partes)...")
    chunks, duracao_total = dividir_audio_em_chunks(caminho_audio)

    segmentos_totais = []
    historico_envios = []
    segundos_processados = 0.0
    tempo_inicio = time.time()

    for idx, (caminho_chunk, offset_segundos, duracao_chunk) in enumerate(chunks, start=1):
        aguardar_cota_disponivel(duracao_chunk, historico_envios, status_placeholder, respeitar_limite)

        segmentos = transcrever_chunk_groq(client, caminho_chunk)
        for seg in segmentos:
            # seg pode vir como objeto ou dict, dependendo da versão do SDK
            inicio = seg["start"] if isinstance(seg, dict) else seg.start
            fim = seg["end"] if isinstance(seg, dict) else seg.end
            texto = seg["text"] if isinstance(seg, dict) else seg.text
            segmentos_totais.append({
                "start": inicio + offset_segundos,
                "end": fim + offset_segundos,
                "text": texto.strip(),
            })

        historico_envios.append((time.time(), duracao_chunk))
        segundos_processados += duracao_chunk

        try:
            os.remove(caminho_chunk)
        except OSError:
            pass

        fracao = min(segundos_processados / duracao_total, 1.0) if duracao_total > 0 else 0.0
        decorrido = time.time() - tempo_inicio
        eta = (decorrido / fracao - decorrido) if fracao > 0.01 else None

        progress_bar.progress(fracao)
        texto_eta = f"ETA: {formatar_duracao(eta)}" if eta is not None else "ETA: calculando..."
        status_placeholder.info(
            f"Transcrevendo via Groq... {fracao * 100:.1f}% "
            f"(parte {idx}/{len(chunks)} — {formatar_duracao(segundos_processados)} / "
            f"{formatar_duracao(duracao_total)}) — {texto_eta}"
        )

    progress_bar.progress(1.0)
    status_placeholder.success(f"Transcrição concluída em {formatar_duracao(time.time() - tempo_inicio)}.")
    return segmentos_totais


def montar_transcricao_formatada(segmentos: list) -> str:
    linhas = []
    for seg in segmentos:
        inicio = segundos_para_mmss(seg["start"])
        fim = segundos_para_mmss(seg["end"])
        linhas.append(f"[{inicio} - {fim}] {seg['text']}")
    return "\n".join(linhas)


# --------------------------------------------------------------------------
# Sugestão de cortes via LLM da Groq
# --------------------------------------------------------------------------

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
st.caption("Transcrição via Groq Whisper + sugestões de cortes de 2 a 3 minutos usando IA da Groq")


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
    st.subheader("Transcrição (Groq Whisper)")
    respeitar_limite = st.checkbox(
        "Respeitar limite gratuito da Groq (pausa automática)",
        value=True,
        help=(
            "Deixe marcado se você usa o plano gratuito da Groq. O app vai "
            "pausar automaticamente para não estourar o teto de ~2h de "
            "áudio por hora. Desmarque só se tiver um plano pago com "
            "limites maiores."
        ),
    )
    st.caption(
        "No plano gratuito, transcrever não custa dinheiro, mas tem um "
        "teto de uso por hora. Um episódio de ~10h pode levar algumas "
        "horas de relógio no total, sem gastar nada."
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

    client = Groq(api_key=api_key)
    progress_bar = st.progress(0.0)
    status = st.empty()
    try:
        segmentos = transcrever_audio_groq(client, caminho_temp, progress_bar, status, respeitar_limite)
        st.session_state.segmentos = segmentos

        if not segmentos:
            st.warning("Nenhum trecho de fala foi identificado no áudio.")
        else:
            transcricao_formatada = montar_transcricao_formatada(segmentos)
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
