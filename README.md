# Grindingcast — Gerador de Cortes

App em Streamlit que transcreve um episódio de podcast (MP3, WAV ou M4A)
usando a **API Whisper da Groq** (gratuita, sem custo em dinheiro) e usa
um modelo de linguagem da Groq para sugerir cortes de 2 a 3 minutos para
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

## Sobre o limite gratuito da Groq (importante para episódios longos)

A transcrição via Groq **não custa dinheiro** no plano gratuito, mas tem
um teto de uso: aproximadamente **7.200 segundos (2 horas) de áudio por
hora corrida**, e 2.000 requisições por dia.

Para dar conta disso, o app:
- Divide o áudio em partes de 10 minutos.
- Envia cada parte para a Groq, uma de cada vez.
- **Pausa automaticamente** quando o uso na última hora está perto do
  teto, e retoma sozinho assim que há cota disponível de novo.
- Mostra barra de progresso, porcentagem e ETA em tempo real (incluindo
  o tempo de pausa).

Na prática, para um episódio de ~10 horas, isso significa que a
transcrição pode levar **várias horas de relógio no total** — mas sem
gastar nada. Se você tiver um plano pago da Groq com limites maiores,
pode desmarcar "Respeitar limite gratuito da Groq" na barra lateral para
pular as pausas.

> **Atenção ao rodar no Streamlit Community Cloud:** sessões muito longas
> (várias horas) podem ser interrompidas por timeout de inatividade ou
> queda de conexão do navegador/aba. Para episódios muito longos, rodar o
> app localmente (`streamlit run app.py` no seu computador, com a aba
> aberta) tende a ser mais confiável do que deixar rodando no navegador
> via Streamlit Cloud.

## Instalação

Requer Python 3.9+ e o **ffmpeg** instalado no sistema (usado pelo
`pydub` para dividir o áudio em partes).

```bash
# instalar o ffmpeg (uma vez, no sistema)
# Windows: https://ffmpeg.org/download.html (adicionar ao PATH)
# Mac:     brew install ffmpeg
# Linux:   sudo apt install ffmpeg

pip install -r requirements.txt
```

Se for publicar no **Streamlit Community Cloud**, o arquivo `packages.txt`
incluído neste projeto já instala o `ffmpeg` automaticamente no servidor
— não precisa fazer nada extra.

## Como rodar

```bash
streamlit run app.py
```

O navegador abrirá automaticamente em http://localhost:8501

## Upload de arquivos grandes (até 1GB)

O limite de upload já vem configurado em `.streamlit/config.toml` para
**1GB** (o padrão do Streamlit é só 200MB). Isso é lido automaticamente
quando você roda o app na mesma pasta (local ou na nuvem).

## Como usar

1. Na barra lateral, confirme que a chave da Groq foi carregada (✅).
2. Faça upload do episódio (MP3, WAV ou M4A).
3. Digite o título do jogo / tema do episódio.
4. Clique em "Transcrever e gerar cortes".
5. Acompanhe a barra de progresso, a porcentagem e o ETA — episódios
   longos podem ter pausas automáticas (explicadas acima).
6. Veja a tabela de cortes sugeridos e clique em "Baixar cortes em Excel".

## Modelos usados

- Transcrição: `whisper-large-v3-turbo` (API de áudio gratuita da Groq).
- Sugestão de cortes: `llama-3.3-70b-versatile` (API de chat gratuita da
  Groq).

Consulte https://console.groq.com/docs/rate-limits para os limites atuais
de uso gratuito da Groq.

## Limitações conhecidas

- O teto de uso gratuito da Groq pode tornar a transcrição de episódios
  muito longos (~10h) um processo de várias horas de relógio (mas $0 de
  custo).
- O app não faz o corte físico do áudio/vídeo — ele apenas indica os
  timestamps e o motivo de cada corte sugerido, para você editar depois em
  qualquer editor de vídeo.
