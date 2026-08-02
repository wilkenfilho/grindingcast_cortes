# Grindingcast — Gerador de Cortes

App em Streamlit que transcreve um episódio de podcast (MP3) **localmente**,
usando Whisper (via `faster-whisper`, sem gastar créditos/tokens da Groq), e
usa a IA da Groq (gratuita) só para sugerir cortes de 2 a 3 minutos para
redes sociais, com título e justificativa para cada corte. Exporta o
resultado em Excel.

## ⚠️ Segurança da chave de API

**Revogue imediatamente qualquer chave da Groq que você já tenha colado em
chats, prints ou repositórios**, em https://console.groq.com/keys, e gere
uma nova.

A chave é lida via **`st.secrets`**, nunca escrita no código:

1. Copie `.streamlit/secrets.toml.example` para `.streamlit/secrets.toml`.
2. Edite o novo arquivo e coloque sua chave:
   ```toml
   GROQ_API_KEY = "sua_chave_aqui"
   ```
3. Confirme que `.streamlit/secrets.toml` está no `.gitignore` (já incluído
   neste projeto) para nunca ser enviado a um repositório.

Se for publicar no **Streamlit Community Cloud**, não suba o
`secrets.toml`: cadastre a chave em *App settings → Secrets* no painel do
Streamlit Cloud, usando o mesmo formato acima.

## Instalação

Requer Python 3.9+.

```bash
pip install -r requirements.txt
```

> **Nota sobre o Whisper local:** na primeira execução, o `faster-whisper`
> baixa automaticamente os pesos do modelo escolhido (ex.: `small`) do
> Hugging Face Hub — é necessário internet só nesse primeiro download;
> depois disso o modelo fica em cache local e funciona offline. Modelos
> maiores (`medium`, `large-v3`) são mais precisos, porém bem mais lentos
> em CPU — se não tiver GPU NVIDIA com CUDA, prefira `tiny`, `base` ou
> `small`.

## Episódios longos (arquivos grandes, até 1GB)

O limite de upload já vem configurado em `.streamlit/config.toml` para
**1GB** (o padrão do Streamlit é só 200MB). Isso é lido automaticamente
quando você roda `streamlit run app.py` na mesma pasta.

Para um podcast de ~10h em CPU, prefira os modelos `tiny`, `base` ou
`small` na barra lateral — modelos `medium`/`large-v3` sem GPU podem levar
muitas horas para transcrever um episódio desse tamanho. Se tiver GPU
NVIDIA com CUDA, selecione `device = cuda` para acelerar bastante e poder
usar modelos maiores.

## Como rodar

```bash
streamlit run app.py
```

O navegador abrirá automaticamente em http://localhost:8501

## Como usar

1. Na barra lateral, confirme que a chave da Groq foi carregada (✅) e
   escolha o tamanho do modelo Whisper, o dispositivo (`cpu`/`cuda`) e a
   precisão.
2. Faça upload do MP3 do episódio.
3. Digite o título do jogo / tema do episódio.
4. Clique em "Transcrever e gerar cortes".
5. Acompanhe a barra de progresso, a porcentagem e o ETA da transcrição
   local (o tempo depende do tamanho do modelo, da duração do episódio e
   do seu hardware).
6. Veja a tabela de cortes sugeridos e clique em "Baixar cortes em Excel".

## Modelos usados

- Transcrição: **Whisper local** via `faster-whisper` (você escolhe o
  tamanho: `tiny`, `base`, `small`, `medium`, `large-v3`) — roda no seu
  computador, sem custo de API.
- Sugestão de cortes: `llama-3.3-70b-versatile` (API de chat gratuita da
  Groq).

Consulte https://console.groq.com/docs/rate-limits para os limites atuais
de uso gratuito da Groq (aplicável apenas à etapa de geração dos cortes).

## Dicas de performance (Whisper local)

- CPU sem GPU: prefira `tiny`/`base`/`small` com `compute_type = int8`.
- GPU NVIDIA com CUDA: selecione `device = cuda` e `compute_type = float16`
  (ou `int8_float16`) para ganhos grandes de velocidade, podendo usar
  `medium`/`large-v3` com boa performance.
- Episódios muito longos (2h+) podem levar bastante tempo em CPU — a ETA
  exibida na tela ajuda a estimar o tempo total.

## Limitações conhecidas

- A qualidade/velocidade da transcrição depende do hardware do seu
  computador (CPU/GPU) e do tamanho do modelo escolhido.
- O app não faz o corte físico do áudio/vídeo — ele apenas indica os
  timestamps e o motivo de cada corte sugerido, para você editar depois em
  qualquer editor de vídeo.
