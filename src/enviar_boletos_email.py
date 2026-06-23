"""
ENVIO DIÁRIO DE BOLETOS POR E-MAIL (9h) — WBA → e-mail in-thread ao sacado
================================================================================
Todos os dias de manhã:
  1. Login no WBA + Gestão de Cobrança → GESTÃO DE TÍTULOS.
  2. Filtra os títulos: VENCIMENTO [dia útil anterior → +6 meses]
     E DATA DE CRIAÇÃO DO BORDERÔ [dia útil anterior → dia útil anterior].
  3. Para cada cedente, baixa os boletos.
  4. Agrupa por SACADO (lido do "Pagador" do PDF) e RESPONDE, na mesma thread,
     o e-mail "CONFIRMAÇÃO DE COMPRA - <cedente>" enviado naquele dia, anexando
     o(s) boleto(s) + o texto fixo de cobrança.
  5. Sacado sem e-mail OU sem a thread daquele dia → NÃO envia, vira pendência.
  6. Manda um resumo (enviados / pendências) por e-mail ao operador.

Execução local (navegador visível, sem enviar nada):
  BOLETOS_EMAIL_HEADLESS=false python src/enviar_boletos_email.py --dry-run
"""
import os
import sys
import ssl
import json
import smtplib
import calendar
from pathlib import Path
from datetime import datetime
from email.message import EmailMessage

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

# Carrega o .env ANTES de importar o wba (que lê WBA_*/CEDENTES/DOWNLOAD_DIR no import).
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except Exception:
    pass

import wba                                # noqa: E402
import email_boleto as eb                 # noqa: E402
from playwright.sync_api import sync_playwright   # noqa: E402

VENC_MESES_FRENTE = int(os.getenv("BOLETOS_EMAIL_VENC_MESES", "6"))
HEADLESS = os.getenv("BOLETOS_EMAIL_HEADLESS", "true").lower() not in ("false", "0", "nao", "não")


# ─────────────────────────────────────────────
# DATAS
# ─────────────────────────────────────────────

def _mais_meses(d, n):
    m = d.month - 1 + n
    y = d.year + m // 12
    m = m % 12 + 1
    dia = min(d.day, calendar.monthrange(y, m)[1])
    return d.replace(year=y, month=m, day=dia)


def calcular_datas():
    """Datas do dia: 'dia anterior' = último dia ÚTIL (borderô é criado em dia útil)."""
    hoje = datetime.today().replace(hour=0, minute=0, second=0, microsecond=0)
    ontem = wba.dia_util_anterior(hoje)
    fmt = "%d/%m/%Y"
    return {
        "ontem": ontem,
        "venc_de": ontem.strftime(fmt),
        "venc_ate": _mais_meses(ontem, VENC_MESES_FRENTE).strftime(fmt),
        "criacao_de": ontem.strftime(fmt),
        "criacao_ate": ontem.strftime(fmt),
    }


# ─────────────────────────────────────────────
# FILTRO (vencimento + criação do borderô)
# ─────────────────────────────────────────────

def aplicar_filtros_email(page, datas):
    """Abre 'Filtrar títulos' e preenche os DOIS intervalos de data."""
    print("  🔍 Abrindo drawer de filtros...")
    page.locator("button:has-text('Filtrar títulos')").first.click()
    page.wait_for_timeout(1000)

    print(f"  🔍 Vencimento: {datas['venc_de']} → {datas['venc_ate']}")
    wba.preencher_data(page, "data-vencimento-cobranca-de", datas["venc_de"])
    wba.preencher_data(page, "data-vencimento-cobranca-ate", datas["venc_ate"])

    print(f"  🔍 Criação do borderô: {datas['criacao_de']} → {datas['criacao_ate']}")
    wba.preencher_data(page, "input-data-criacao-bordero-de", datas["criacao_de"])
    wba.preencher_data(page, "input-data-criacao-bordero-ate", datas["criacao_ate"])

    print("  ⏳ Aplicando filtro...")
    page.locator("button:has-text('Filtrar')").last.click()
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(1500)


# ─────────────────────────────────────────────
# AGRUPAR POR SACADO + RESUMO
# ─────────────────────────────────────────────

def _agrupar_por_sacado(arquivos):
    """Lê o pagador de cada PDF e agrupa {cnpj: {'nome', 'arquivos': [...]}}.
    PDFs sem pagador identificável vão para a chave especial '?'."""
    grupos = {}
    for arq in arquivos:
        cnpj, nome = eb.pagador_do_boleto(arq)
        chave = cnpj or "?"
        g = grupos.setdefault(chave, {"nome": nome, "arquivos": []})
        g["arquivos"].append(arq)
        if nome and not g["nome"]:
            g["nome"] = nome
    return grupos


def enviar_resumo(resultado):
    """Manda um e-mail de resumo (enviados / pendências) ao operador. Best-effort."""
    para = os.getenv("EMAIL_RESUMO_PARA", os.getenv("EMAIL_FROM") or os.getenv("EMAIL_LOGIN"))
    login = os.getenv("EMAIL_LOGIN")
    senha = os.getenv("EMAIL_SENHA")
    host = os.getenv("EMAIL_SMTP_HOST", "email-ssl.com.br")
    port = int(os.getenv("EMAIL_SMTP_PORT", "465"))
    if not (para and login and senha):
        return

    linhas = ["RESUMO — boletos por e-mail", f"Data: {datetime.now():%d/%m/%Y %H:%M}", ""]
    linhas.append(f"✅ Enviados: {len(resultado['enviados'])}")
    for e in resultado["enviados"]:
        linhas.append(f"   • {e['cedente']} | {e['sacado']} → {e['detalhe']}")
    linhas.append("")
    linhas.append(f"⚠️  Pendências: {len(resultado['pendencias'])}")
    for p in resultado["pendencias"]:
        linhas.append(f"   • {p['cedente']} | sacado {p['sacado']} ({p.get('nome') or '-'}) "
                      f"| {len(p.get('arquivos', []))} boleto(s) | {p['motivo']}")
    corpo = "\n".join(linhas)

    try:
        msg = EmailMessage()
        msg["From"] = os.getenv("EMAIL_FROM", login)
        msg["To"] = para
        marca = "[TESTE] " if os.getenv("EMAIL_TESTE", "").strip() else ""
        msg["Subject"] = f"{marca}Resumo boletos por e-mail — {datetime.now():%d/%m/%Y}"
        msg.set_content(corpo)
        with smtplib.SMTP_SSL(host, port, context=ssl.create_default_context(), timeout=45) as s:
            s.login(login, senha)
            s.send_message(msg)
        print(f"  📧 Resumo enviado para {para}")
    except Exception as e:
        print(f"  ⚠️  Falha ao enviar resumo: {e}")


# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────

def main(cedente_filtro=None, dry_run=False):
    hoje = datetime.today()
    if not wba.is_dia_util(hoje) and not dry_run:
        print(f"⛔ {hoje:%d/%m/%Y} não é dia útil — nada a fazer.")
        return

    datas = calcular_datas()
    cedentes = wba.CEDENTES
    if cedente_filtro:
        cedentes = [c for c in wba.CEDENTES if cedente_filtro.upper() in c.upper()]

    print("=" * 64)
    print("   ENVIO DE BOLETOS POR E-MAIL (resposta na thread de confirmação)")
    print("=" * 64)
    print(f"📅 Vencimento : {datas['venc_de']} → {datas['venc_ate']}")
    print(f"📅 Criação BD : {datas['criacao_de']} → {datas['criacao_ate']}")
    print(f"👥 Cedentes   : {', '.join(cedentes) or '(nenhum configurado)'}")
    print(f"🧪 Modo       : {'DRY-RUN (não envia)' if dry_run else 'ENVIO REAL'}"
          + (f"  | EMAIL_TESTE={os.getenv('EMAIL_TESTE')}" if os.getenv('EMAIL_TESTE') else ""))
    print(f"🖥️  Headless   : {HEADLESS}")

    Path(wba.DOWNLOAD_DIR).mkdir(parents=True, exist_ok=True)

    try:
        planilha = eb.carregar_planilha()
    except Exception as e:
        print(f"❌ Não consegui carregar a planilha de sacados: {e}")
        planilha = {}

    resultado = {"enviados": [], "pendencias": []}

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=HEADLESS, timeout=120_000,
            args=["--disable-blink-features=AutomationControlled", "--disable-dev-shm-usage"],
        )
        context = browser.new_context(accept_downloads=True)
        page = context.new_page()

        try:
            wba.fazer_login(page)
        except Exception as e:
            print(f"❌ FALHA NO LOGIN: {e}")
            browser.close()
            return

        for cedente in cedentes:
            print(f"\n{'─' * 64}\n  ▶  {cedente}\n{'─' * 64}")
            try:
                wba.resetar_para_home(page)
                wba.navegar_gestao_titulos(page)
                aplicar_filtros_email(page, datas)

                if not wba.expandir_e_selecionar(page, cedente):
                    print("  ⚠️  Cedente sem títulos no filtro — pulando.")
                    continue

                wba.abrir_aba_boletos(page)
                arquivos = wba.baixar_boletos(page, cedente)
                if not arquivos:
                    print("  ⚠️  Nenhum boleto baixado.")
                    continue

                grupos = _agrupar_por_sacado(arquivos)
                print(f"  👤 {len(grupos)} sacado(s) neste cedente")

                for cnpj, g in grupos.items():
                    nome = g["nome"]
                    arqs = g["arquivos"]
                    if cnpj == "?":
                        resultado["pendencias"].append({
                            "cedente": cedente, "sacado": "?", "nome": nome,
                            "arquivos": arqs, "motivo": "pagador não identificado no PDF"})
                        continue

                    dados = planilha.get(cnpj) or {}
                    email_sac = dados.get("email") or ""
                    nome = nome or dados.get("nome")
                    if not email_sac:
                        resultado["pendencias"].append({
                            "cedente": cedente, "sacado": cnpj, "nome": nome,
                            "arquivos": arqs, "motivo": "sacado sem e-mail na planilha"})
                        continue

                    thread = eb.achar_thread_confirmacao(
                        email_sac, cedente, desde=datas["ontem"].date(), ate=datas["ontem"].date())
                    if not thread:
                        resultado["pendencias"].append({
                            "cedente": cedente, "sacado": cnpj, "nome": nome, "arquivos": arqs,
                            "motivo": f"sem thread CONFIRMAÇÃO DE COMPRA p/ {email_sac} em {datas['criacao_de']}"})
                        continue

                    if dry_run:
                        to_l, cc_l = eb.destinatarios_resposta(thread, email_sac)
                        print(f"  🔬 [DRY] {nome or cnpj} ← {len(arqs)} boleto(s)")
                        print(f"          To: {', '.join(to_l)} | Cc: {', '.join(cc_l) or '-'}")
                        print(f"          thread: {thread['subject'][:60]}")
                        resultado["enviados"].append({
                            "cedente": cedente, "sacado": cnpj,
                            "detalhe": f"[dry] To {', '.join(to_l)} | Cc {', '.join(cc_l) or '-'} ({len(arqs)} boleto(s))"})
                        continue

                    ok, det = eb.responder_com_boleto(email_sac, arqs, thread)
                    print(f"  {'✅' if ok else '❌'} {nome or cnpj}: {det}")
                    (resultado["enviados"] if ok else resultado["pendencias"]).append(
                        {"cedente": cedente, "sacado": cnpj, "nome": nome,
                         "arquivos": arqs, "detalhe": det, "motivo": det})
            except Exception as e:
                print(f"  ❌ Erro isolado em '{cedente}': {e}")
                resultado["pendencias"].append(
                    {"cedente": cedente, "sacado": "-", "arquivos": [], "motivo": f"erro: {e}"})

        browser.close()

    # ── RESUMO ──
    print(f"\n{'=' * 64}\n   RESUMO\n{'=' * 64}")
    print(f"   ✅ Enviados  : {len(resultado['enviados'])}")
    print(f"   ⚠️  Pendências: {len(resultado['pendencias'])}")
    for p in resultado["pendencias"]:
        print(f"      • {p['cedente']} | {p.get('sacado')} ({p.get('nome') or '-'}) → {p['motivo']}")
    try:
        reg = Path(wba.DOWNLOAD_DIR) / f"resumo_email_{datetime.now():%Y%m%d_%H%M%S}.json"
        reg.write_text(json.dumps(resultado, ensure_ascii=False, default=str, indent=2), encoding="utf-8")
    except Exception:
        pass

    if not dry_run:
        enviar_resumo(resultado)


if __name__ == "__main__":
    args = sys.argv[1:]
    dry = "--dry-run" in args or os.getenv("BOLETOS_EMAIL_DRY_RUN", "").lower() in ("1", "true")
    ced = next((a for a in args if not a.startswith("--")), None)
    main(cedente_filtro=ced, dry_run=dry)
