# boletos-email-automation

Robô diário (9h, dias úteis) que pega os **boletos emitidos no dia anterior** no
WBA e os **responde, na mesma thread**, ao e-mail `CONFIRMAÇÃO DE COMPRA - <cedente>`
enviado ao sacado no dia em que o borderô foi criado — anexando o(s) boleto(s) e
um texto fixo de cobrança.

Repositório **isolado** e self-contained: sem dependência de WhatsApp/Seu Gênio
ou Google Cloud Storage. (O núcleo de automação do WBA foi reaproveitado, e
validado, do robô de boletos→WhatsApp.)

## Como funciona
1. Login no WBA → Cobrança → Gestão de Cobrança → **GESTÃO DE TÍTULOS**.
2. Filtra: **Vencimento** `[dia útil anterior → +6 meses]`
   e **Data de criação do borderô** `[dia útil anterior → dia útil anterior]`.
3. Baixa os boletos por cedente.
4. Agrupa por **sacado** (CNPJ/CPF lido do "Pagador" do PDF) e, para cada um,
   acha a thread de confirmação daquele dia e **responde a todos** (`Re:` —
   To = sacado, Cc = cedente + Luiz, exatamente quem estava na confirmação)
   anexando o(s) boleto(s).
5. Sacado **sem e-mail** ou **sem a thread do dia** → não envia, vira pendência.
6. Manda um resumo (enviados/pendências) por e-mail ao operador.

## Estrutura
```
src/
  wba.py                  # núcleo WBA: login, navegação, seleção, download
  email_boleto.py         # planilha, pagador do PDF, IMAP (thread), SMTP (resposta)
  enviar_boletos_email.py # orquestração (entrypoint)
docker/
  Dockerfile              # Cloud Run Job (CMD = python enviar_boletos_email.py)
  requirements.txt
.env.example              # modelo de configuração (copie p/ .env)
```

## Rodar local
```bash
python -m venv .venv && . .venv/Scripts/activate   # Windows: .venv\Scripts\activate
pip install -r docker/requirements.txt
playwright install chromium
cp .env.example .env        # preencha WBA_* e EMAIL_*

# DRY-RUN: baixa, identifica sacado/e-mail/thread e IMPRIME — não envia nada
BOLETOS_EMAIL_HEADLESS=false python src/enviar_boletos_email.py --dry-run
# um cedente só:
BOLETOS_EMAIL_HEADLESS=false python src/enviar_boletos_email.py "MUUH OUTSOURCING" --dry-run
```
Validação segura: `--dry-run` → depois `EMAIL_TESTE=seu@email` (envia tudo p/ você
com `[TESTE]`) → por fim remova `EMAIL_TESTE` para ir ao vivo.

## Deploy — Cloud Run Job + Scheduler 9h (em produção)
Projeto `smartpagamentos`, região `us-central1`. Imagem
`gcr.io/smartpagamentos/boletos-email:latest` (build via `cloudbuild.yaml`).
Config do Job vem de um `.env.yaml` (gerado do `.env`, **gitignored**).

```bash
# 1) Build da imagem
gcloud builds submit --config cloudbuild.yaml --project smartpagamentos .

# 2) .env.yaml (mapa YAML com as MESMAS chaves do .env, sem EMAIL_TESTE p/ produção)

# 3) Criar o Job (resources p/ o Chromium headless)
gcloud run jobs create boletos-email \
  --image gcr.io/smartpagamentos/boletos-email:latest \
  --region us-central1 --project smartpagamentos \
  --env-vars-file .env.yaml --memory 2Gi --cpu 2 \
  --max-retries 0 --task-timeout 1800

# 4) Deixar o SA dos schedulers executar o Job
gcloud run jobs add-iam-policy-binding boletos-email \
  --member="serviceAccount:bot-boletos@smartpagamentos.iam.gserviceaccount.com" \
  --role="roles/run.invoker" --region us-central1 --project smartpagamentos

# 5) Scheduler 9h seg-sex (America/Sao_Paulo) → dispara o Job
gcloud scheduler jobs create http boletos-email-900 \
  --location us-central1 --project smartpagamentos \
  --schedule "0 9 * * 1-5" --time-zone "America/Sao_Paulo" \
  --uri "https://us-central1-run.googleapis.com/v2/projects/smartpagamentos/locations/us-central1/jobs/boletos-email:run" \
  --http-method POST \
  --oauth-service-account-email "bot-boletos@smartpagamentos.iam.gserviceaccount.com"
```

Operação:
```bash
# rodar manualmente quando quiser
gcloud run jobs execute boletos-email --region us-central1 --project smartpagamentos
# pausar / retomar o automático
gcloud scheduler jobs pause  boletos-email-900 --location us-central1 --project smartpagamentos
gcloud scheduler jobs resume boletos-email-900 --location us-central1 --project smartpagamentos
# validar na nuvem sem atingir ninguém (modo teste)
gcloud run jobs update boletos-email --update-env-vars EMAIL_TESTE=voce@email --region us-central1 --project smartpagamentos
```

## Configuração
Veja `.env.example`. Segredos (`.env`) **nunca** vão para o git (ver `.gitignore`).
