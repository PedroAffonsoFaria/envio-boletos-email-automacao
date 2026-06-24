"""
ENVIO DE BOLETOS POR E-MAIL — resposta na thread de confirmação (Locaweb)
================================================================================
Para cada SACADO, responde — na MESMA thread — o e-mail "CONFIRMAÇÃO DE COMPRA
- <CEDENTE>" que foi enviado no dia em que o borderô foi criado, anexando o(s)
boleto(s) baixado(s) do WBA e o texto fixo de cobrança.

  - Identifica o sacado lendo o "Pagador" de dentro do PDF do boleto (pdfplumber).
  - Acha a thread original em INBOX.enviadas (IMAP) pelo destinatário + data.
  - Responde com In-Reply-To/References → cai como "Re:" na conversa do sacado.
  - Se NÃO achar a thread daquele dia, NÃO envia (devolve como pendência).

Config por variáveis de ambiente (.env):
  EMAIL_SMTP_HOST, EMAIL_SMTP_PORT, EMAIL_IMAP_HOST, EMAIL_LOGIN, EMAIL_SENHA,
  EMAIL_FROM, EMAIL_PASTA_ENVIADOS, EMAIL_SHEET_ID, EMAIL_CC_FIXO (opcional),
  EMAIL_TESTE (opcional — redireciona tudo p/ um endereço de teste).
"""
import os
import re
import io
import csv
import ssl
import time
import email
import imaplib
import smtplib
import unicodedata
from pathlib import Path
from datetime import date, timedelta
from email.header import decode_header, make_header
from email.message import EmailMessage
from email.utils import getaddresses, make_msgid, formatdate

import requests

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except Exception:
    pass

# ─── Planilha de sacados ───
SHEET_ID = os.getenv("EMAIL_SHEET_ID", "1u8rRIxESjNT5Rs-ZEa7JecMbbkBZ84AhGJEX-1nAS7Q")
SHEET_CSV = f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/export?format=csv"
CC_FIXO = os.getenv("EMAIL_CC_FIXO", "").strip()   # vazio = não copia ninguém

CNPJ_CPF = r"\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2}|\d{3}\.\d{3}\.\d{3}-\d{2}"
_MESES_EN = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
             "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

# Texto fixo que acompanha os boletos (definido pelo Pedro).
CORPO_TXT = """Bom dia,

Seguem, em anexo, os boletos para pagamento.



Atenciosamente,

Pedro Affonso

SMART SECURITIZADORA
"""

CORPO_HTML = """<div style="font-family: Verdana, Geneva, sans-serif; font-size: 10pt;">
<p>Bom dia,</p>
<p>Seguem, em anexo, os boletos para pagamento.</p>
<p>&nbsp;</p>
<p>Atenciosamente,</p>
<p><b>Pedro Affonso</b></p>
<p><b>SMART SECURITIZADORA</b></p>
</div>"""


def _so_digitos(s):
    return re.sub(r"\D", "", s or "")


def _sem_acento(s):
    return "".join(c for c in unicodedata.normalize("NFD", s or "")
                   if unicodedata.category(c) != "Mn")


def _data_imap(d):
    """Data no formato IMAP (dd-Mon-yyyy) com mês em inglês (independe de locale)."""
    return f"{d.day:02d}-{_MESES_EN[d.month - 1]}-{d.year}"


# ─────────────────────────────────────────────
# PLANILHA — e-mail do sacado por CNPJ/CPF
# ─────────────────────────────────────────────

def carregar_planilha():
    """Baixa o CSV da planilha → {documento_digitos: {'nome','email','cc'}}."""
    r = requests.get(SHEET_CSV, timeout=30)
    r.raise_for_status()
    r.encoding = "utf-8"
    mapa = {}
    for row in csv.DictReader(io.StringIO(r.text)):
        doc = _so_digitos(row.get("CPF/CNPJ"))
        if not doc:
            continue
        mapa[doc] = {
            "nome": (row.get("Nome") or "").strip(),
            "email": (row.get("Email") or "").strip(),
            "cc": (row.get("E-mail Cedente (CC)") or "").strip(),
        }
    return mapa


# ─────────────────────────────────────────────
# IDENTIFICAR O SACADO PELO PDF DO BOLETO
# ─────────────────────────────────────────────

def pagador_do_boleto(pdf_path):
    """Lê o boleto e devolve (documento_digitos, nome) do PAGADOR (sacado).
    Layout WBA/Bradesco: 'Pagador <NOME> - CNPJ 99.999.999/9999-99' (mesma linha).
    Evita 'Recibo do Pagador' (cabeçalho), 'Beneficiário' (cedente) e
    'Sacador/Avalista' (cedente). Se não identificar com segurança, devolve None
    (vira pendência — melhor que chutar o sacado errado)."""
    import pdfplumber
    try:
        with pdfplumber.open(pdf_path) as pdf:
            full = "\n".join((pg.extract_text() or "") for pg in pdf.pages)
    except Exception:
        return None, None

    # 1) "Pagador <nome> - CNPJ/CPF <doc>" na mesma linha (formato do WBA)
    m = re.search(r"\bPagador[ \t]+(.+?)\s*-\s*(?:CNPJ|CPF)\s*(" + CNPJ_CPF + r")", full)
    if m:
        return _so_digitos(m.group(2)), m.group(1).strip()
    # 2) "Pagador <nome> <doc>" (sem o rótulo CNPJ), ainda na mesma linha
    m = re.search(r"\bPagador[ \t]+(.+?)\s+(" + CNPJ_CPF + r")", full)
    if m:
        return _so_digitos(m.group(2)), m.group(1).strip()
    return None, None


# ─────────────────────────────────────────────
# ACHAR A THREAD DE CONFIRMAÇÃO (IMAP)
# ─────────────────────────────────────────────

def _decode(s):
    try:
        return str(make_header(decode_header(s or "")))
    except Exception:
        return s or ""


def _score_cedente(subject, cedente_nome):
    """Quão bem o assunto bate com o cedente (tokens em comum). Desempata threads."""
    if not cedente_nome:
        return 0
    subj = _sem_acento(subject).upper()
    tokens = [t for t in re.split(r"\W+", _sem_acento(cedente_nome).upper())
              if len(t) >= 3 and t not in ("LTDA", "LIMPEZA", "ARTIGOS", "DE")]
    return sum(1 for t in tokens if t in subj)


def achar_thread_confirmacao(email_sac, cedente_nome=None, desde=None, ate=None):
    """Procura, em INBOX.enviadas, o e-mail 'CONFIRMAÇÃO DE COMPRA' enviado ao sacado
    no intervalo [desde, ate]. Devolve {'message_id','references','subject'} ou None.
    Casa por destinatário + período; o assunto/cedente só desempata."""
    host = os.getenv("EMAIL_IMAP_HOST", "email-ssl.com.br")
    login = os.getenv("EMAIL_LOGIN")
    senha = os.getenv("EMAIL_SENHA")
    pasta = os.getenv("EMAIL_PASTA_ENVIADOS", "INBOX.enviadas")
    if not (login and senha and email_sac):
        return None

    desde = desde or (date.today() - timedelta(days=1))
    ate = ate or desde

    M = None
    try:
        M = imaplib.IMAP4_SSL(host, 993, ssl_context=ssl.create_default_context())
        M.login(login, senha)
        M.select(pasta)
        # SINCE é inclusivo; BEFORE é exclusivo → ate + 1 dia para incluir 'ate'.
        crit = ["SINCE", _data_imap(desde),
                "BEFORE", _data_imap(ate + timedelta(days=1)),
                "HEADER", "TO", email_sac]
        typ, data = M.search(None, *crit)
        if typ != "OK" or not data or not data[0]:
            return None

        melhor, melhor_score = None, -1
        for num in reversed(data[0].split()):   # mais recentes primeiro
            typ, msgdata = M.fetch(
                num, "(BODY.PEEK[HEADER.FIELDS (SUBJECT MESSAGE-ID REFERENCES TO CC)])")
            if typ != "OK" or not msgdata or not msgdata[0]:
                continue
            hdr = email.message_from_bytes(msgdata[0][1])
            subj = _decode(hdr.get("Subject"))
            if "CONFIRMA" not in _sem_acento(subj).upper() or "COMPRA" not in subj.upper():
                continue
            score = _score_cedente(subj, cedente_nome)
            if score > melhor_score:
                melhor_score = score
                melhor = {
                    "message_id": (hdr.get("Message-ID") or "").strip(),
                    "references": (hdr.get("References") or "").strip(),
                    "subject": subj,
                    "to": _decode(hdr.get("To")),    # destinatário original (sacado)
                    "cc": _decode(hdr.get("Cc")),    # cópias originais (cedente + Luiz)
                }
            # match forte de cedente (token em comum) → fica com esse e para
            if score >= 1:
                break
        return melhor
    except Exception:
        return None
    finally:
        try:
            if M is not None:
                M.logout()
        except Exception:
            pass


# ─────────────────────────────────────────────
# REPLY-ALL — destinatários da resposta
# ─────────────────────────────────────────────

def _enderecos(*headers):
    """Extrai os e-mails de um ou mais cabeçalhos To/Cc."""
    out = []
    for h in headers:
        if not h:
            continue
        for _nome, addr in getaddresses([h]):
            a = (addr or "").strip()
            if a:
                out.append(a)
    return out


def _nossos_enderecos():
    return {x.lower() for x in (os.getenv("EMAIL_LOGIN", ""),
                                os.getenv("EMAIL_FROM", "")) if x}


def destinatarios_resposta(thread, email_sac=None):
    """Reply-all (como o 'Responder a todos' do webmail): To = destinatário
    original (sacado); Cc = cópias originais (cedente + Luiz) + EMAIL_CC_FIXO.
    Remove os nossos próprios endereços e duplicatas. NÃO aplica o modo de teste
    (isso é feito em responder_com_boleto). Devolve (to_list, cc_list)."""
    nossos = _nossos_enderecos()
    to_list = _enderecos(thread.get("to")) if thread else []
    if not to_list and email_sac:
        to_list = [email_sac]
    to_list = [a for a in to_list if a.lower() not in nossos] or to_list

    cc_bruto = _enderecos(thread.get("cc")) if thread else []
    if CC_FIXO:
        cc_bruto.append(CC_FIXO)
    visto = {a.lower() for a in to_list} | nossos
    cc_list = []
    for a in cc_bruto:
        al = a.lower()
        if al and al not in visto:
            visto.add(al)
            cc_list.append(a)
    return to_list, cc_list


# ─────────────────────────────────────────────
# RESPONDER COM O(S) BOLETO(S) ANEXADO(S)
# ─────────────────────────────────────────────

def _salvar_em_enviados(msg):
    """Grava cópia da resposta na pasta Enviadas (aparece no webmail). Best-effort."""
    host = os.getenv("EMAIL_IMAP_HOST", "email-ssl.com.br")
    login = os.getenv("EMAIL_LOGIN")
    senha = os.getenv("EMAIL_SENHA")
    pasta = os.getenv("EMAIL_PASTA_ENVIADOS", "INBOX.enviadas")
    try:
        M = imaplib.IMAP4_SSL(host, 993, ssl_context=ssl.create_default_context())
        M.login(login, senha)
        M.append(pasta, r"(\Seen)", imaplib.Time2Internaldate(time.time()), msg.as_bytes())
        M.logout()
        return True
    except Exception:
        return False


def responder_com_boleto(email_sac, anexos, thread, cc_list=None):
    """Monta e envia a resposta in-thread ao sacado com o(s) boleto(s) em anexo.
    `thread` = dict de achar_thread_confirmacao(). Devolve (ok, detalhe)."""
    host = os.getenv("EMAIL_SMTP_HOST", "email-ssl.com.br")
    port = int(os.getenv("EMAIL_SMTP_PORT", "465"))
    login = os.getenv("EMAIL_LOGIN")
    senha = os.getenv("EMAIL_SENHA")
    remetente = os.getenv("EMAIL_FROM", login)
    if not (login and senha):
        return False, "EMAIL_LOGIN/EMAIL_SENHA não configurados"
    if not email_sac:
        return False, "sacado sem e-mail"
    if not thread:
        return False, "sem thread de confirmação"

    # Reply-all: To = sacado (destinatário original) + Cc = cedente + Luiz
    # (cópias originais da confirmação) + EMAIL_CC_FIXO.
    to_list, cc = destinatarios_resposta(thread, email_sac)
    if cc_list:   # extras passados explicitamente
        cc += [c.strip() for c in cc_list if c and c.strip()]

    # Modo teste: redireciona TUDO p/ EMAIL_TESTE (não atinge ninguém real)
    teste = os.getenv("EMAIL_TESTE", "").strip()
    prefixo = ""
    if teste:
        to_list, cc = [teste], []
        prefixo = "[TESTE] "

    if not to_list:
        return False, "sem destinatário (To) para a resposta"

    subj = thread.get("subject") or "CONFIRMAÇÃO DE COMPRA"
    if not subj.strip().lower().startswith("re:"):
        subj = "Re: " + subj

    msg = EmailMessage()
    msg["From"] = remetente
    msg["To"] = ", ".join(to_list)
    if cc:
        msg["Cc"] = ", ".join(dict.fromkeys(cc))   # sem duplicar
    msg["Subject"] = prefixo + subj
    # Date + Message-ID próprios (higiene; ajuda exibição/threading em alguns clientes)
    msg["Date"] = formatdate(localtime=True)
    _dom = remetente.split("@")[-1] if (remetente and "@" in remetente) else "smartdf.com.br"
    msg["Message-ID"] = make_msgid(domain=_dom)
    mid = thread.get("message_id")
    if mid:
        msg["In-Reply-To"] = mid
        refs = (thread.get("references", "") + " " + mid).strip()
        msg["References"] = refs
    msg.set_content(CORPO_TXT)
    msg.add_alternative(CORPO_HTML, subtype="html")

    anexados = 0
    for p in anexos or []:
        p = Path(p)
        if p.exists():
            msg.add_attachment(p.read_bytes(), maintype="application",
                               subtype="pdf", filename=p.name)
            anexados += 1
    if anexados == 0:
        return False, "nenhum anexo válido"

    destinatarios = to_list + cc
    ultimo = ""
    for tentativa in range(3):
        try:
            ctx = ssl.create_default_context()
            with smtplib.SMTP_SSL(host, port, context=ctx, timeout=45) as s:
                s.login(login, senha)
                s.send_message(msg, to_addrs=destinatarios)
            _salvar_em_enviados(msg)
            return True, (f"respondido p/ {', '.join(to_list)} "
                          f"({anexados} boleto(s); cc: {', '.join(cc) or '-'})")
        except Exception as e:
            ultimo = f"{type(e).__name__}: {e}"
            time.sleep(2 * (tentativa + 1))
    return False, ultimo
