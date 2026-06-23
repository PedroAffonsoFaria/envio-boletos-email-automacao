"""
NÚCLEO WBA — login, navegação e download de boletos (Gestão de Cobrança)
================================================================================
Funções de automação do WBA reaproveitadas (e validadas) do robô de boletos.
Self-contained: SEM dependência de WhatsApp/Seu Gênio ou Google Cloud Storage.

O fluxo que chama estas funções está em enviar_boletos_email.py.
"""
import os
import time
from datetime import datetime, timedelta
from playwright.sync_api import TimeoutError as PWTimeout

# ─────────────────────────────────────────────
# CONFIG (variáveis de ambiente)
# ─────────────────────────────────────────────
WBA_URL   = os.getenv("WBA_URL", "https://new.wba.com.br/login/be9f33e9-b23e-4854-8004-8744f1877bf3")
WBA_LOGIN = os.getenv("WBA_LOGIN")
WBA_SENHA = os.getenv("WBA_SENHA")

DOWNLOAD_DIR = os.getenv("DOWNLOAD_DIR", "/tmp/boletos_email")
TIMEOUT = 60_000

CEDENTES_ENV = os.getenv("CEDENTES", "")
CEDENTES = [c.strip() for c in CEDENTES_ENV.split(",") if c.strip()] if CEDENTES_ENV else []

FERIADOS = [
    "21/04/2025", "01/05/2025", "19/06/2025", "07/09/2025",
    "12/10/2025", "02/11/2025", "15/11/2025", "25/12/2025",
    "01/01/2026", "20/02/2026", "03/04/2026", "21/04/2026",
    "01/05/2026", "04/06/2026", "07/09/2026", "12/10/2026",
    "02/11/2026", "15/11/2026", "25/12/2026",
]


# ─────────────────────────────────────────────
# DATAS
# ─────────────────────────────────────────────

def is_feriado(data: datetime) -> bool:
    return data.strftime("%d/%m/%Y") in FERIADOS


def is_dia_util(data: datetime) -> bool:
    return data.weekday() < 5 and not is_feriado(data)


def dia_util_anterior(data: datetime) -> datetime:
    d = data - timedelta(days=1)
    while not is_dia_util(d):
        d -= timedelta(days=1)
    return d


# ─────────────────────────────────────────────
# LOGIN
# ─────────────────────────────────────────────

def fazer_login(page):
    if not WBA_LOGIN or not WBA_SENHA:
        raise RuntimeError("WBA_LOGIN e WBA_SENHA devem estar configurados")

    print("\n🔐 Verificando login no WBA...")
    page.goto(WBA_URL, timeout=60_000)
    page.wait_for_load_state("networkidle", timeout=60_000)
    print(f"   URL atual: {page.url}")

    try:
        page.wait_for_selector('input[type="password"]', timeout=10_000, state="visible")
    except PWTimeout:
        print("   ✅ Já logado!")
        return

    print("   Tela de login detectada — preenchendo credenciais...")
    email_input = page.locator(
        'input[type="email"], input[name*="email" i], input[name*="user" i], '
        'input[name*="login" i], input[type="text"]:visible'
    ).first
    email_input.wait_for(state="visible", timeout=TIMEOUT)
    email_input.click(click_count=3)
    email_input.fill(WBA_LOGIN)

    pass_input = page.locator('input[type="password"]').first
    pass_input.click(click_count=3)
    pass_input.fill(WBA_SENHA)

    page.locator('button:has-text("Entrar")').first.click()
    page.wait_for_load_state("networkidle", timeout=60_000)
    page.wait_for_timeout(10000)

    try:
        page.wait_for_selector('input[type="password"]', timeout=10_000, state="visible")
        try:
            page.screenshot(path="debug_login_falhou.png", full_page=True)
            print("   📸 Screenshot do login salvo: debug_login_falhou.png")
        except Exception:
            pass
        raise RuntimeError("Ainda na tela de login — verifique credenciais/captcha/2FA.")
    except PWTimeout:
        pass

    print("   ✅ Login realizado!")


# ─────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────

def preencher_data(page, field_id, data):
    """Preenche campo Angular Material Datepicker (DD/MM/AAAA) dígito por dígito."""
    campo = page.locator(f"#{field_id}")
    campo.click()
    page.wait_for_timeout(300)
    campo.press("Control+a")
    page.wait_for_timeout(100)
    campo.press("Delete")
    page.wait_for_timeout(100)
    for digit in data.replace("/", ""):
        campo.press(digit)
        page.wait_for_timeout(50)
    page.keyboard.press("Escape")
    page.wait_for_timeout(300)


def resetar_para_home(page):
    print("  🔄 Voltando para home...")
    try:
        page.bring_to_front()
        page.wait_for_timeout(500)
    except Exception:
        pass
    try:
        btn_voltar = page.locator(
            "fa-icon.fa-arrow-left, .fa-arrow-left, button:has(fa-icon[data-icon='arrow-left'])"
        ).first
        if btn_voltar.is_visible(timeout=2000):
            btn_voltar.click()
            page.wait_for_timeout(1000)
    except Exception:
        pass
    page.keyboard.press("Escape")
    page.wait_for_timeout(500)
    page.goto("https://new.wba.com.br/home/bem-vindo-texto")
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(1500)


# ─────────────────────────────────────────────
# NAVEGAÇÃO ATÉ GESTÃO DE TÍTULOS
# ─────────────────────────────────────────────

def navegar_gestao_titulos(page):
    """Cobrança (menu lateral) → Gestão de Cobrança (header) → card GESTÃO DE TÍTULOS."""
    print("  📂 Clicando em Cobrança (menu lateral)...")
    tentativas, max_tentativas = 0, 3
    while tentativas < max_tentativas:
        try:
            menu = page.locator("#menu-lateral-COBRANCA")
            menu.wait_for(state="visible", timeout=5000)
            menu.click()
            page.wait_for_load_state("networkidle", timeout=10000)
            page.wait_for_timeout(1500)
            break
        except PWTimeout:
            tentativas += 1
            if tentativas < max_tentativas:
                print(f"  ⚠️  Menu não apareceu — retry {tentativas}/{max_tentativas-1}...")
                page.wait_for_timeout(2000)
                try:
                    page.goto("https://new.wba.com.br/home/bem-vindo-texto", wait_until="networkidle")
                    page.wait_for_timeout(2000)
                except Exception:
                    pass
            else:
                raise RuntimeError(f"Menu não apareceu após {max_tentativas} tentativas")

    print("  📂 Clicando em Gestão de Cobrança (header)...")
    gestao = page.get_by_text("Gestão de Cobrança", exact=False).first
    gestao.wait_for(state="visible", timeout=TIMEOUT)
    gestao.click()

    print("  📂 Clicando no card GESTÃO DE TÍTULOS...")
    card = page.get_by_text("GESTÃO DE TÍTULOS", exact=False).first
    card.wait_for(state="visible", timeout=TIMEOUT)
    card.click()
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(1500)


# ─────────────────────────────────────────────
# EXPANDIR CEDENTE + SELECIONAR TÍTULOS
# ─────────────────────────────────────────────

def expandir_e_selecionar(page, cedente):
    print(f"    → Procurando cedente: {cedente}")
    cards = page.locator(".card-cedente").all()
    print(f"    📍 Total de cards na página: {len(cards)}")
    card_alvo = None
    for i, card in enumerate(cards):
        try:
            h6_texto = card.locator("h6").first.inner_text()
            print(f"    📍 Card {i+1}: {h6_texto[:50]}")
            if cedente.upper() in h6_texto.upper():
                card_alvo = card
                print("    ✓ Card encontrado!")
                break
        except Exception as e:
            print(f"    ⚠️  Erro ao ler card {i+1}: {str(e)[:50]}")
            continue

    if card_alvo is None:
        print(f"    ❌ Cedente '{cedente}' não encontrado entre {len(cards)} cards")
        return False

    btn = card_alvo.locator("button.submenu__fechado")
    if btn.count() == 0:
        btn = card_alvo.locator("div.btn__mostrarMais button")
    btn.first.click()
    page.wait_for_timeout(1500)

    # Aumentar itens por página para 50 (cedentes com >20 títulos numa página só)
    try:
        print("    → Aumentando itens por página para 50...")
        sucesso = page.evaluate("""
            () => {
                const allElements = document.querySelectorAll('*');
                let btnItens = null;
                for (let el of allElements) {
                    if (el.innerText && el.innerText.includes('Itens por página')) {
                        if (el.tagName === 'BUTTON' || el.querySelector('button')) {
                            btnItens = el.tagName === 'BUTTON' ? el : el.querySelector('button');
                            break;
                        }
                    }
                }
                if (!btnItens) { return false; }
                btnItens.click();
                return new Promise(resolve => {
                    setTimeout(() => {
                        const allText = document.querySelectorAll('*');
                        let opcao50 = null;
                        for (let el of allText) {
                            if (el.innerText === '50' || (el.textContent === '50' && el.offsetParent !== null)) {
                                const style = window.getComputedStyle(el);
                                if (style.display !== 'none' && style.visibility !== 'hidden') {
                                    opcao50 = el; break;
                                }
                            }
                        }
                        if (opcao50) { opcao50.click(); resolve(true); } else { resolve(false); }
                    }, 500);
                });
            }
        """)
        if sucesso:
            page.wait_for_timeout(1500)
            print("    ✓ Configurado para 50 itens por página!")
        else:
            print("    ⚠️  Não conseguiu aumentar para 50 itens")
    except Exception as e:
        print(f"    ⚠️  Erro ao aumentar itens por página: {str(e)}")

    pagina = 1
    while True:
        print(f"    → Página {pagina}: selecionando títulos...")
        try:
            resultado_js = page.evaluate("""
            (cedente) => {
                const cards = document.querySelectorAll('.card-cedente');
                for (const card of cards) {
                    const h6 = card.querySelector('h6');
                    if (h6 && h6.innerText.toUpperCase().includes(cedente.toUpperCase())) {
                        let el = card.nextElementSibling;
                        while (el) {
                            const checkbox = el.querySelector('thead input[type="checkbox"]');
                            if (checkbox) { checkbox.click(); return {sucesso: true, local: 'sibling'}; }
                            el = el.nextElementSibling;
                        }
                        const checkbox = card.querySelector('thead input[type="checkbox"]');
                        if (checkbox) { checkbox.click(); return {sucesso: true, local: 'card'}; }
                        return {sucesso: false, local: 'nenhum'};
                    }
                }
                return {sucesso: false, local: 'card_nao_encontrado'};
            }
        """, cedente)
        except Exception as e:
            print(f"    ⚠️  Erro ao executar JavaScript: {type(e).__name__}: {str(e)[:100]}")
            return False

        checkbox_clicado = resultado_js.get('sucesso', False)
        local = resultado_js.get('local', 'desconhecido')
        print(f"    ℹ️  Checkbox: {'✓ encontrado' if checkbox_clicado else '❌ não encontrado'} (local: {local})")
        page.wait_for_timeout(500)

        if not checkbox_clicado:
            if pagina == 1:
                return False
            break

        try:
            btn_next = page.locator('button[aria-label="Next page"]:not([disabled])').first
            if btn_next.is_visible(timeout=2000) and btn_next.is_enabled():
                print(f"    ▶  Navegando para página {pagina + 1}...")
                btn_next.click()
                page.wait_for_timeout(1500)
                pagina += 1
            else:
                break
        except Exception:
            break

    print(f"    ✅ Títulos selecionados para '{cedente}' ({pagina} página(s))")
    return True


# ─────────────────────────────────────────────
# ABA BOLETOS + DOWNLOAD
# ─────────────────────────────────────────────

def abrir_aba_boletos(page):
    print("  📄 Abrindo aba Boletos...")
    page.locator("text=Boletos").first.click()
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(1500)


def configurar_itens_por_pagina_50(page):
    """Ajusta o mat-paginator para 50 itens/página (ou o maior disponível)."""
    try:
        selects = page.locator('.mat-mdc-paginator-page-size-select mat-select')
        if selects.count() == 0:
            selects = page.locator('mat-paginator mat-select')
        n = selects.count()
        if n == 0:
            print("    ⚠️  mat-paginator não encontrado — segue com paginação")
            return False

        aplicado = False
        for i in range(n):
            sel = selects.nth(i)
            try:
                if not sel.is_visible():
                    continue
                sel.scroll_into_view_if_needed()
                sel.click()
                page.wait_for_timeout(600)
                opcoes = page.locator("mat-option")
                idx_50 = idx_maior = None
                maior_val = -1
                for j in range(opcoes.count()):
                    txt = (opcoes.nth(j).inner_text() or "").strip()
                    if not txt.isdigit():
                        continue
                    val = int(txt)
                    if val == 50:
                        idx_50 = j
                    if val > maior_val:
                        maior_val, idx_maior = val, j
                escolhido = idx_50 if idx_50 is not None else idx_maior
                if escolhido is not None:
                    valor = 50 if idx_50 is not None else maior_val
                    opcoes.nth(escolhido).click()
                    page.wait_for_timeout(1500)
                    print(f"    ✓ {valor} itens por página aplicado (mat-paginator)!")
                    aplicado = True
                else:
                    page.keyboard.press("Escape")
            except Exception as e:
                print(f"    ⚠️  mat-select #{i}: {str(e)[:60]}")
                try:
                    page.keyboard.press("Escape")
                except Exception:
                    pass
        if not aplicado:
            print("    ⚠️  Não consegui ajustar itens/página — segue com paginação")
        return aplicado
    except Exception as e:
        print(f"    ⚠️  Erro ao ajustar itens/página: {str(e)[:100]}")
        return False


def _fechar_modais(page):
    """Fecha modais/overlays abertos (cdk-overlay) para não bloquear cliques."""
    try:
        for _ in range(2):
            page.keyboard.press("Escape")
            page.wait_for_timeout(150)
        backdrop = page.locator(
            ".cdk-overlay-backdrop-showing, .cdk-overlay-backdrop, .modal-backdrop").first
        if backdrop.count() and backdrop.is_visible():
            try:
                backdrop.click(timeout=1000, force=True)
            except Exception:
                pass
            page.wait_for_timeout(200)
    except Exception:
        pass


def baixar_boletos(page, cedente):
    """Baixa todos os boletos da visão atual. Devolve a lista de caminhos salvos."""
    arquivos = []
    erros_download = []
    pagina = 1

    configurar_itens_por_pagina_50(page)

    while True:
        print(f"    📄 Página {pagina}: buscando botões de download...")
        page.wait_for_timeout(1000)
        total = page.locator('td[data-label="Boleto"] fa-icon').count()
        if total == 0:
            print(f"    ⚠️  Página {pagina}: nenhum botão encontrado")
            break
        print(f"    📄 {total} boleto(s) na página {pagina}")

        for i in range(total):
            sucesso = False
            ultimo_erro = ""
            for tentativa in range(2):
                try:
                    if tentativa > 0:
                        _fechar_modais(page)
                        page.wait_for_timeout(400)
                    botao = page.locator('td[data-label="Boleto"] fa-icon').nth(i)
                    botao.scroll_into_view_if_needed(timeout=10000)
                    with page.expect_download(timeout=20000) as dl_info:
                        botao.click(timeout=15000)
                        page.wait_for_timeout(800)
                        try:
                            btn_confirmar = page.locator(
                                'button:has-text("Confirmar"), '
                                'button:has-text("OK"), '
                                'button:has-text("Sim")'
                            ).first
                            if btn_confirmar.is_visible(timeout=2000):
                                print("    🔔 Modal detectado — confirmando...")
                                btn_confirmar.click()
                        except Exception:
                            pass
                    download = dl_info.value
                    nome = download.suggested_filename
                    if not nome or not nome.lower().endswith(".pdf"):
                        url = download.url
                        if "/" in url:
                            nome_url = url.split("/")[-1].split("?")[0]
                            nome = nome_url if (nome_url and "." in nome_url) else \
                                f"boleto_{cedente.replace(' ', '_')}_{int(time.time())}_{i+1}.pdf"
                        else:
                            nome = f"boleto_{cedente.replace(' ', '_')}_{int(time.time())}_{i+1}.pdf"
                    if not nome.lower().endswith(".pdf"):
                        nome = nome + ".pdf"
                    nome = "".join(c for c in nome if c.isalnum() or c in "._- ").strip() or f"boleto_{i+1}.pdf"
                    destino = os.path.join(DOWNLOAD_DIR, nome)
                    download.save_as(destino)
                    arquivos.append(destino)
                    print(f"    💾 [{i+1}/{total}] Salvo: {nome}")
                    page.wait_for_timeout(500)
                    sucesso = True
                    break
                except Exception as e:
                    ultimo_erro = str(e)[:80]
                    print(f"    ⚠️  Boleto {i+1} (tentativa {tentativa+1}): {ultimo_erro}")
                    _fechar_modais(page)
                    page.wait_for_timeout(500)
            if not sucesso:
                print(f"    ❌ Boleto {i+1}/{total} falhou após retry — seguindo.")
                erros_download.append(f"boleto {i+1}/{total}: {ultimo_erro or 'motivo desconhecido'}")

        try:
            btn_next = page.locator(
                'mat-paginator button.mat-mdc-paginator-navigation-next:not([disabled]), '
                'button[aria-label="Próxima página"]:not([disabled]), '
                'button[aria-label="Next page"]:not([disabled])'
            ).first
            if btn_next.count() and btn_next.is_enabled():
                btn_next.scroll_into_view_if_needed()
                btn_next.click()
                page.wait_for_timeout(1500)
                pagina += 1
            else:
                break
        except Exception:
            break

    return arquivos
