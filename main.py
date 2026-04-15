import customtkinter as ctk
from tkinter import filedialog
import json
import hashlib
import asyncio
import aiohttp
from aiohttp import ClientTimeout
from pathlib import Path
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError, Error
import logging
import re
import sys
import threading
import queue
import ctypes
from typing import Optional, List, Dict, Tuple, Any

# --- CONFIGURAÇÃO INICIAL E VARIÁVEIS GLOBAIS ---
try:
    with open('config.json', 'r', encoding='utf-8') as f:
        CONFIG = json.load(f)
except Exception as e:
    print(f"Erro ao carregar config.json: {e}")
    CONFIG = {}

try:
    with open('course-urls.json', 'r', encoding='utf-8') as f:
        COURSE_URLS = json.load(f)
except Exception:
    COURSE_URLS = []

ESTRATEGIA_HOST = "estrategiaconcursos.com.br"
# Páginas onde o aluno vê os cursos/matérias contratadas ("Meus cursos" / assinaturas)
MINHAS_MATRICULAS_URLS = [
    "https://www.estrategiaconcursos.com.br/app/dashboard/cursos",
    "https://www.estrategiaconcursos.com.br/app/dashboard/assinaturas",
]
CATALOGO_MAPEADO_FILE = Path("meus-cursos-mapeados.json")


def normalize_estrategia_url(raw: str) -> str:
    """Remove aspas, caracteres invisíveis, quebras de linha e garante https."""
    if not raw:
        return ""
    u = raw.strip().strip('"').strip("'")
    u = u.replace("\u200b", "").replace("\ufeff", "").replace("\u200c", "")
    for sep in ("\n", "\r", "\t"):
        if sep in u:
            u = u.split(sep)[0].strip()
    if " " in u and "http" in u.lower():
        for part in u.split():
            if part.lower().startswith(("http://", "https://")):
                u = part.strip()
                break
    if not u:
        return ""
    if not re.match(r"^https?://", u, re.I):
        u = "https://" + u
    return u.strip()


def is_estrategia_domain(url: str) -> bool:
    return ESTRATEGIA_HOST in url.lower()


def looks_like_package_page_url(url: str) -> bool:
    """
    URL aceita na aba Pacotes: lista de cursos (Meus Cursos), pacote comercial ou área /app/.../pacote...
    """
    if not is_estrategia_domain(url):
        return False
    lower = url.lower()
    # Meus cursos (lista de matrículas) — mesmo padrão do site na área logada
    if re.search(r"/app/dashboard/cursos(?:/|\?|#|$)", lower):
        return True
    # /pacote/, /pacotes/, /pacotes-2023/, /pacote?x=...
    if re.search(r"/pacotes?(?:/|\?|#|$|-)", lower):
        return True
    if "/app/" in lower and "pacote" in lower:
        return True
    if "/curso/" in lower and "pacote" in lower:
        return True
    return False


def looks_like_individual_course_url(url: str) -> bool:
    """URL da fila Matérias: curso na área logada ou URL clássica .../cursos/<id>/..."""
    if not is_estrategia_domain(url):
        return False
    lower = url.lower()
    # Área do aluno (ex.: .../app/dashboard/cursos/229171/aulas)
    if re.search(r"/app/dashboard/cursos/\d+", lower):
        return True
    if re.search(r"/cursos/\d+", lower):
        return True
    return "/cursos/" in lower


def absolutize_estrategia_href(href: str) -> Optional[str]:
    href = (href or "").strip().split("#")[0]
    if not href:
        return None
    if href.lower().startswith("http"):
        return href
    if href.startswith("/"):
        return "https://www.estrategiaconcursos.com.br" + href
    return None


def is_course_queue_url(url: str) -> bool:
    """Link que leva a uma página de aulas de um curso (para a fila de download)."""
    if not is_estrategia_domain(url):
        return False
    lower = url.lower()
    if re.search(r"/app/dashboard/cursos/\d+", lower):
        return True
    if re.search(r"/cursos/\d+", lower):
        return True
    return False


def ensure_course_aulas_url(url: str) -> str:
    """Garante sufixo /aulas nas URLs do dashboard, compatível com o downloader."""
    u = (url or "").strip().split("#")[0].rstrip("/")
    if "/aulas" in u.lower():
        return u if u.startswith("http") else "https://www.estrategiaconcursos.com.br" + u
    if re.search(r"/app/dashboard/cursos/\d+$", u):
        return u + "/aulas"
    return url


async def collect_courses_with_metadata(page, logger) -> list[dict]:
    """
    Extrai título + URL de cada curso visível (cards em Meus cursos / páginas equivalentes).
    """
    result: list[dict] = []
    seen: set[str] = set()

    async def push_item(titulo: str, href: str) -> None:
        full = absolutize_estrategia_href(href)
        if not full or not is_course_queue_url(full):
            return
        full = ensure_course_aulas_url(full)
        titulo = (titulo or "").strip() or "Sem título"
        if full in seen:
            return
        seen.add(full)
        result.append({"titulo": titulo, "url": full})

    try:
        cards = await page.locator('section[id^="card"]').all()
        for card in cards:
            link_el = card.locator(
                'a[href*="/app/dashboard/cursos/"], a[href*="/cursos/"]'
            ).first
            if await link_el.count() == 0:
                continue
            href = await link_el.get_attribute("href") or ""
            titulo = ""
            for sel in ('h1', 'h2', '[class*="Title"]', 'a[href*="cursos"]'):
                loc = card.locator(sel).first
                if await loc.count() == 0:
                    continue
                try:
                    raw = await loc.inner_text()
                    titulo = (raw or "").strip().split("\n")[0].strip()
                except Exception:
                    titulo = ""
                if titulo:
                    break
            await push_item(titulo, href)
    except Exception as e:
        logger.warning(f"Leitura por cards: {e}")

    if not result:
        try:
            links = await page.locator(
                'a[href*="/app/dashboard/cursos/"], div.containerCursos a[href*="/cursos/"]'
            ).all()
            for link in links:
                href = await link.get_attribute("href") or ""
                try:
                    titulo = (await link.inner_text() or "").strip().split("\n")[0].strip()
                except Exception:
                    titulo = ""
                await push_item(titulo or "Curso", href)
        except Exception as e:
            logger.warning(f"Leitura por links soltos: {e}")

    logger.info(f"Mapeados {len(result)} curso(s) nesta página.")
    return result


async def collect_course_urls_from_listing_page(page, logger) -> list[str]:
    """
    Coleta URLs de cursos em páginas de listagem (Meus Cursos, pacote antigo containerCursos, etc.).
    """
    urls: list[str] = []
    seen: set[str] = set()
    selectors = [
        'div.containerCursos a[href*="/cursos/"]',
        'section[id^="card"] a[href*="cursos"]',
        'a[href*="/app/dashboard/cursos/"]',
    ]
    for sel in selectors:
        try:
            links = await page.locator(sel).all()
        except Exception as e:
            logger.debug(f"Seletor ignorado {sel!r}: {e}")
            continue
        for link in links:
            href = await link.get_attribute("href")
            full = absolutize_estrategia_href(href)
            if not full or not is_course_queue_url(full):
                continue
            if full not in seen:
                seen.add(full)
                urls.append(full)
    return urls


MSG_PACOTE_INVALIDO = (
    "URL de pacote / lista não reconhecida. Exemplos válidos:\n"
    "  • Meus cursos: https://www.estrategiaconcursos.com.br/app/dashboard/cursos\n"
    "  • Pacote catálogo: .../pacotes/... ou .../pacote/...\n"
    "  • Área app pacote: .../app/.../pacote/...\n"
    "  • Slug pacote: .../curso/...-pacote-.../\n"
    "Domínio: estrategiaconcursos.com.br"
)

MSG_CURSO_INVALIDO = (
    "URL de matéria inválida. Exemplos válidos:\n"
    "  • https://www.estrategiaconcursos.com.br/app/dashboard/cursos/229171/aulas\n"
    "  • https://www.estrategiaconcursos.com.br/cursos/12345/.../aulas\n"
    "Cole só o link (uma linha), sem texto extra antes ou depois."
)


# --- REDIRECIONAMENTO DO LOG PARA A INTERFACE GRÁFICA ---
class QueueHandler(logging.Handler):
    def __init__(self, log_queue):
        super().__init__()
        self.log_queue = log_queue
    def emit(self, record):
        self.log_queue.put(self.format(record))

class PrintRedirector:
    def __init__(self, logger_instance):
        self.logger = logger_instance
    def write(self, message):
        if message.strip(): self.logger.info(message.strip())
    def flush(self):
        pass

# --- INÍCIO DA SEÇÃO DE LÓGICA COMPLETA DO DOWNLOADER ---
PROGRESS_FILE = Path('progress.json')
RESOLUCOES_DISPONIVEIS = ['720p', '480p', '360p']

# Configuração dos tipos de materiais (Livros). url_keys: substrings do href (ordem 1→3→2 no detector).
PDF_TYPES_CONFIG = {
    1: {
        'name': 'versão simplificada',
        'url_keys': ('pdfSimplificado', 'pdf-simplificado', 'pdfsimplificado'),
    },
    2: {
        'name': 'versão original',
        'url_keys': ('/pdf/download', 'pdf/download'),
    },
    3: {
        'name': 'marcação dos aprovados',
        'url_keys': ('pdfGrifado', 'pdf-grifado', 'pdfgrifado'),
    },
}


def livro_pdf_type_from_url(full_pdf_url: str) -> Optional[int]:
    """Identifica o tipo de livro (1/2/3) pelo href. Original (2) por último — path mais genérico."""
    u = (full_pdf_url or '').lower()
    for t_id in (1, 3, 2):
        info = PDF_TYPES_CONFIG.get(t_id)
        if not info:
            continue
        for key in info['url_keys']:
            if key.lower() in u:
                return t_id
    return None

# Configuração dos materiais extras
EXTRA_MATERIALS = {
    'Baixar Resumo': 'Resumo',
    'Baixar Slides': 'Slides',
    'Baixar Mapa Mental': 'Mapa Mental'
}

def _strip_windows_illegal_trailing(name: str) -> str:
    """
    No Windows, arquivo/pasta não pode terminar com espaço ou ponto (API recusa / errno 2).
    """
    t = name.strip()
    while t and t[-1] in ". ":
        t = t[:-1].strip()
    return t if t else "_"


def sanitize_filename(filename, max_length=200):
    sanitized = ''.join('_' if c in '<>:"/\\|?*' else c for c in filename).strip()
    sanitized = re.sub(r'\s+', ' ', sanitized)
    if len(sanitized) > max_length:
        sanitized = sanitized[:max_length].rsplit(' ', 1)[0].rstrip('.')
    if sys.platform == "win32":
        sanitized = _strip_windows_illegal_trailing(sanitized)
    return sanitized


def build_pdf_save_path(
    course_dir: Path,
    lesson_folder_label: str,
    descriptive_stem: str,
    variant_label: str,
) -> Path:
    """
    Pasta curta ``Aula NN`` + nome de arquivo com título; encurta se o caminho total
    no Windows se aproximar do limite (~260).
    """
    lesson_dir = course_dir / lesson_folder_label
    suffix = f" ({variant_label}).pdf"
    stem = sanitize_filename(descriptive_stem.strip(), max_length=180)
    if sys.platform == "win32":
        base_prefix = len(str(lesson_dir)) + 1
        max_total = 248
        budget = max_total - base_prefix - len(suffix)
        if budget < 28:
            budget = 28
        if len(stem) > budget:
            sig = hashlib.sha1(descriptive_stem.encode("utf-8")).hexdigest()[:8]
            keep = max(20, budget - len(sig) - 1)
            stem = f"{stem[:keep]}_{sig}"
    return lesson_dir / f"{stem}{suffix}"


def _video_basename_max_chars(lesson_dir: Path) -> int:
    """
    Limite seguro para o nome do .mp4 (só o basename), para não estourar MAX_PATH no Windows.
    """
    try:
        try:
            plen = len(str(lesson_dir.resolve(strict=False)))
        except TypeError:
            plen = len(str(lesson_dir.resolve()))
    except (OSError, ValueError, TypeError):
        plen = len(str(lesson_dir))
    margin = 20
    cap = 248 - plen - margin
    if sys.platform != "win32":
        return max(120, min(cap, 220))
    return max(72, min(cap, 155))


def build_safe_video_filename(
    lesson_dir: Path,
    lesson_name: str,
    j: int,
    video_title: str,
    used_resolution: str,
) -> str:
    """
    Monta nome de arquivo .mp4 que cabe no caminho total (Windows).
    Títulos enormes da plataforma viram prefixo + hash para não falhar com [Errno 2].
    """
    max_total = _video_basename_max_chars(lesson_dir)
    ln = sanitize_filename(lesson_name, max_length=80)
    vt = sanitize_filename(video_title, max_length=400)
    res = sanitize_filename(str(used_resolution).replace("/", "_"), max_length=24)
    ext = ".mp4"
    head = f"{ln} - Vídeo {j} "
    tail = f" [{res}]{ext}"
    max_mid = max_total - len(head) - len(tail)
    if max_mid < 10:
        max_mid = 10
    candidate = head + vt + tail
    if len(candidate) <= max_total:
        return sanitize_filename(candidate, max_total)
    sig = hashlib.sha1(f"{ln}|{j}|{vt}|{res}".encode("utf-8")).hexdigest()[:8]
    keep = max_mid - len(sig) - 2
    if keep < 6:
        keep = 6
    stub = vt[:keep].rstrip(" ._")
    name = f"{head}{stub}_{sig}{tail}"
    return sanitize_filename(name, max_total)


async def minimize_browser_via_os(page):
    """
    Minimiza a janela usando a API do Windows.
    """
    try:
        await page.bring_to_front()
        await asyncio.sleep(0.5)
        hwnd = ctypes.windll.user32.GetForegroundWindow()
        ctypes.windll.user32.ShowWindow(hwnd, 6) # 6 = SW_MINIMIZE
    except Exception as e:
        print(f"Erro ao minimizar via OS: {e}")


async def apply_post_login_window_behavior(page, logger):
    """
    Por padrão mantém o Chromium visível para você acompanhar.
    Só minimiza se 'minimizeAfterLogin' estiver true em config.json.
    """
    if CONFIG.get("headless", False):
        return
    if CONFIG.get("minimizeAfterLogin", False):
        logger.info("Minimizando navegador (opção ativa nas Configurações).")
        await minimize_browser_via_os(page)
        return
    try:
        await page.bring_to_front()
        await asyncio.sleep(0.25)
        if sys.platform == "win32":
            hwnd = ctypes.windll.user32.GetForegroundWindow()
            if hwnd:
                ctypes.windll.user32.ShowWindow(hwnd, 9)  # SW_RESTORE
                ctypes.windll.user32.SetForegroundWindow(hwnd)
    except Exception as e:
        logger.warning(f"Não foi possível trazer o navegador para frente: {e}")


class BrowserSessionDeadError(Exception):
    """Chromium foi fechado, crashou ou a aba/contexto deixou de existir."""


def playwright_session_dead(exc: BaseException) -> bool:
    msg = str(exc).lower()
    needles = (
        "has been closed",
        "target page, context or browser has been closed",
        "browser has been closed",
        "browsercontext.new_page",
        "session closed",
        "connection closed",
        "target closed",
    )
    return any(n in msg for n in needles)


async def page_goto_resilient(
    page,
    url: str,
    logger,
    *,
    wait_until: str = "domcontentloaded",
    timeout_ms: int = 120000,
    attempts: int = 5,
) -> None:
    """
    Repete page.goto em falhas típicas de rede/DNS (ex.: net::ERR_NAME_NOT_RESOLVED).
    """
    if page.is_closed():
        raise BrowserSessionDeadError(
            "A aba já estava fechada (não dá para navegar)."
        )
    last_exc: Optional[BaseException] = None
    for attempt in range(1, attempts + 1):
        try:
            await page.goto(url, wait_until=wait_until, timeout=timeout_ms)
            return
        except Exception as e:
            last_exc = e
            if playwright_session_dead(e):
                raise BrowserSessionDeadError(str(e)) from e
            msg = str(e).lower()
            retriable = any(
                x in msg
                for x in (
                    "err_name_not_resolved",
                    "err_internet_disconnected",
                    "err_connection",
                    "err_connection_reset",
                    "err_timed_out",
                    "timeout",
                    "navigation",
                    "net::err",
                    "getaddrinfo",
                    "cannot connect",
                    "econnrefused",
                )
            )
            if not retriable:
                raise
            if attempt >= attempts:
                raise
            delay = min(45, 5 * attempt)
            logger.warning(
                f"Navegação {attempt}/{attempts} falhou ({type(e).__name__}). "
                f"Aguardando {delay}s (rede/DNS instável?)..."
            )
            await asyncio.sleep(delay)
    if last_exc:
        raise last_exc


async def download_file_aiohttp(url, file_path, logger, retries=8, cookies=None, headers=None):
    """
    Download de vídeo via aiohttp. Remove arquivo parcial em falha e tenta de novo
    (queda de conexão / ContentLengthError são comuns em arquivos grandes).
    """
    # sock_read=None: sem limite entre chunks (vídeos longos); só limita conexão inicial
    aio_timeout = ClientTimeout(total=None, sock_connect=90, sock_read=None)
    for attempt in range(1, retries + 1):
        try:
            if file_path.exists():
                try:
                    file_path.unlink()
                except OSError:
                    pass
            async with aiohttp.ClientSession(cookies=cookies, timeout=aio_timeout) as session:
                async with session.get(url, headers=headers) as response:
                    if response.status != 200:
                        raise RuntimeError(f"HTTP {response.status}")
                    with open(file_path, "wb") as f:
                        async for chunk in response.content.iter_chunked(1024 * 1024):
                            f.write(chunk)
            logger.info(f"Vídeo salvo: {file_path.name}")
            return
        except Exception as e:
            logger.warning(f"Tentativa {attempt}/{retries} no vídeo falhou: {e}")
            try:
                if file_path.exists():
                    file_path.unlink()
            except OSError:
                pass
            if attempt == retries:
                raise RuntimeError("Falha ao baixar vídeo após várias tentativas (rede/CDN).") from e
            await asyncio.sleep(min(60, 5 * attempt))

async def verify_download(file_path, logger):
    if not file_path.exists():
        raise ValueError(f'Arquivo não encontrado.')
    size = file_path.stat().st_size
    if size < 1024:
        raise ValueError(f'Arquivo vazio ou corrompido.')
    logger.info(f'Verificação OK ({size / (1024*1024):.2f} MB)')
    return True


# Vídeo real quase sempre > ~300 KB; abaixo disso consideramos incompleto e baixamos de novo
MIN_VIDEO_FILE_BYTES = 300 * 1024


def video_file_looks_complete(file_path: Path, min_bytes: int = MIN_VIDEO_FILE_BYTES) -> bool:
    try:
        return file_path.is_file() and file_path.stat().st_size >= min_bytes
    except OSError:
        return False


def find_complete_video_for_lesson_slot(
    lesson_dir: Path, lesson_name: str, j: int, video_title: str
) -> Optional[Path]:
    """
    Procura .mp4 já baixado para a mesma aula/índice (qualquer resolução no nome do arquivo).
    """
    if not lesson_dir.is_dir():
        return None
    marker = f" - Vídeo {j} "
    best: Optional[Path] = None
    best_size = 0
    for p in lesson_dir.iterdir():
        if not p.is_file() or p.suffix.lower() != ".mp4":
            continue
        name = p.name
        if lesson_name not in name or marker not in name:
            continue
        if video_title:
            sn = sanitize_filename(video_title)
            if video_title not in name and sn not in name:
                continue
        if video_file_looks_complete(p) and p.stat().st_size > best_size:
            best = p
            best_size = p.stat().st_size
    if best is None and video_title:
        for p in lesson_dir.iterdir():
            if not p.is_file() or p.suffix.lower() != ".mp4":
                continue
            name = p.name
            if lesson_name not in name or marker not in name:
                continue
            if video_file_looks_complete(p) and p.stat().st_size > best_size:
                best = p
                best_size = p.stat().st_size
    return best


def find_complete_video_slot_without_title(
    lesson_dir: Path, lesson_name: str, j: int
) -> Optional[Path]:
    """Indica se o slot j da aula tem algum .mp4 completo (ignora título no nome)."""
    if not lesson_dir.is_dir():
        return None
    marker = f" - Vídeo {j} "
    best: Optional[Path] = None
    best_size = 0
    for p in lesson_dir.iterdir():
        if not p.is_file() or p.suffix.lower() != ".mp4":
            continue
        if lesson_name not in p.name or marker not in p.name:
            continue
        if video_file_looks_complete(p) and p.stat().st_size > best_size:
            best = p
            best_size = p.stat().st_size
    return best


def remove_incomplete_mp4_for_lesson_slot(
    lesson_dir: Path, lesson_name: str, j: int, logger: Optional[logging.Logger] = None,
) -> int:
    """
    Remove apenas arquivos .mp4 do slot j que estão claramente incompletos (abaixo do mínimo).
    Não apaga a pasta da aula nem outros slots.
    """
    if not lesson_dir.is_dir():
        return 0
    marker = f" - Vídeo {j} "
    removed = 0
    for p in list(lesson_dir.iterdir()):
        if not p.is_file() or p.suffix.lower() != ".mp4":
            continue
        if lesson_name not in p.name or marker not in p.name:
            continue
        if video_file_looks_complete(p):
            continue
        try:
            p.unlink(missing_ok=True)
            removed += 1
            if logger:
                logger.info(f"Removido incompleto (< {MIN_VIDEO_FILE_BYTES // 1024} KB): {p.name}")
        except OSError as e:
            if logger:
                logger.warning(f"Não foi possível remover {p.name}: {e}")
    return removed


def count_complete_video_slots(lesson_dir: Path, lesson_name: str, n_videos: int) -> int:
    if n_videos <= 0:
        return 0
    n = 0
    for j in range(1, n_videos + 1):
        if find_complete_video_slot_without_title(lesson_dir, lesson_name, j) is not None:
            n += 1
    return n


async def load_progress():
    try:
        with open(PROGRESS_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

async def save_progress(progress):
    with open(PROGRESS_FILE, 'w', encoding='utf-8') as f:
        json.dump(progress, f, indent=2)

async def get_video_url_by_resolution(page, preferred_resolution, logger):
    try:
        # Espera o player carregar (aumentei o timeout para 60s por segurança)
        await page.wait_for_selector('video.video-react-video', state='attached', timeout=60000)
        video_player_locator = page.locator('video.video-react-video')

        try:
            # Tenta clicar no botão de qualidade via JS (mais seguro contra sobreposição)
            await page.evaluate('''() => { 
                const btn = document.querySelector('.PlayerControl-button'); 
                if (btn) btn.click(); 
            }''')
            await page.wait_for_selector('.PlayerControl-options', state='visible', timeout=5000)
        except Exception:
            pass

        # Seleciona a resolução
        await page.evaluate('''(res) => {
            const options = Array.from(document.querySelectorAll('.PlayerControlOptions-button'));
            const btn = options.find(b => b.textContent && b.textContent.trim().includes(res));
            if (btn) btn.click();
        }''', preferred_resolution.replace('p', ''))
        
        logger.info(f"Qualidade {preferred_resolution} solicitada...")
        await asyncio.sleep(4.0)

        current_url = await video_player_locator.get_attribute('src')
        if not current_url:
            raise Exception("URL do vídeo não encontrada.")

        final_url = current_url
        used_resolution = "padrão"

        if preferred_resolution.replace('p', '') not in final_url:
            forced_url = re.sub(r"/(360|480|720)/", f"/{preferred_resolution.replace('p','')}/", final_url)
            if forced_url != final_url:
                final_url = forced_url
                used_resolution = preferred_resolution
            else:
                match = re.search(r"/(\d{3,4})/", final_url)
                if match: used_resolution = f"{match.group(1)}p"
        else:
            used_resolution = preferred_resolution

        return {'url': final_url, 'resolution': used_resolution}

    except Exception as e:
        logger.error(f"Erro ao obter link do vídeo: {e}")
        try:
            fallback_url = await page.locator('video.video-react-video').get_attribute('src')
            return {'url': fallback_url, 'resolution': 'fallback'}
        except Exception:
            return {'url': None, 'resolution': 'erro'}

def extract_materia_name(course_name):
    if 'Conhecimentos Regionais' in course_name: return 'Conhecimentos Regionais'
    materia = course_name
    materia = re.sub(r'^Concursos da Área Fiscal\s*-\s*', '', materia, flags=re.IGNORECASE)
    materia = re.sub(r'Curso (Completo|Básico) de ', '', materia, flags=re.IGNORECASE)
    materia = re.sub(r'\(Profs?\.?[\w\s,]+\)', '', materia, flags=re.IGNORECASE)
    materia = re.sub(r'\s*-\s*\d{4}(?!\d)', '', materia)
    materia = re.sub(r'\s*\(Pós-Edital\)', '', materia, flags=re.IGNORECASE)
    materia = re.sub(r'Prefeitura [\w\s-]+?-', '', materia, flags=re.IGNORECASE)
    materia = re.sub(r'\([^)]+\)', '', materia)
    materia = re.sub(r' - Área Administrativa', '', materia, flags=re.IGNORECASE)
    materia = re.sub(r'Noções de ', '', materia, flags=re.IGNORECASE)
    materia = re.sub(r'^\s*-\s*|\s*-\s*$', '', materia)
    materia = materia.strip()
    if ':' in materia: materia = materia.split(':')[1].strip()
    return materia or 'Matéria Desconhecida'
    
async def ensure_logged_in(page, logger):
    LOGIN_URL = 'https://www.estrategiaconcursos.com.br/app/auth/login'
    ASSINATURAS_URL = 'https://www.estrategiaconcursos.com.br/app/dashboard/assinaturas'
    
    logger.info("Acessando página de login...")
    await page.goto(LOGIN_URL, wait_until='domcontentloaded', timeout=60000)
    try:
        await page.wait_for_selector('a:has-text("Catálogo de Cursos")', timeout=10000)
        logger.info("Login já ativo.")
    except PlaywrightTimeoutError:
        logger.info("Login necessário. Preenchendo dados...")
        try:
            await page.wait_for_selector('input[name="loginField"]', state='visible', timeout=60000)
            await page.fill('input[name="loginField"]', CONFIG['email'])
            await page.wait_for_selector('input[name="passwordField"]', state='visible', timeout=60000)
            await page.fill('input[name="passwordField"]', CONFIG['senha'])
            
            logger.info("Clique em Entrar...")
            await page.click('button[type="submit"]')
            await page.wait_for_selector('a:has-text("Catálogo de Cursos")', timeout=120000) 
            logger.info("Login realizado!")
        except PlaywrightTimeoutError as e:
            logger.error(f"Tempo esgotado no login.")
            raise Exception("Falha no login.")
    try:
        current_url = page.url
        if "perfil.estrategia.com" in current_url:
            await page.goto(ASSINATURAS_URL, wait_until='domcontentloaded', timeout=60000)
        if "/app/dashboard/cursos" == page.url.replace('https://www.estrategiaconcursos.com.br', ''):
            await page.locator('a:has-text("Catálogo de Cursos")').click()
            await page.wait_for_url("**/app/dashboard/assinaturas", timeout=30000)
    except Exception as e:
        logger.error(f"Erro no redirecionamento: {e}")
        raise

async def process_course_pdf(page, course_url, progress, logger):
    BASE_DIR = Path(CONFIG['pdfConfig']['pastaDownloads'])
    logger.info(f"Abrindo curso (PDF): {course_url}")
    
    await page_goto_resilient(
        page, course_url, logger, wait_until="domcontentloaded", timeout_ms=120000
    )
    if "app/dashboard/cursos" in page.url and not re.search(r'/cursos/\d+/aulas', page.url):
        await page_goto_resilient(
            page, course_url, logger, wait_until="domcontentloaded", timeout_ms=120000
        )

    # "networkidle" em SPA costuma demorar muito; domcontentloaded + lista já basta para PDF.
    try:
        await page.wait_for_load_state("load", timeout=12000)
    except Exception:
        pass

    try:
        course_name_element = await page.wait_for_selector('.CourseInfo-content-title', timeout=30000)
        course_name = await course_name_element.text_content()
    except PlaywrightTimeoutError:
        course_name = await page.title()

    materia_name = extract_materia_name(course_name)
    course_dir = BASE_DIR / sanitize_filename(materia_name)
    course_dir.mkdir(parents=True, exist_ok=True)

    try:
        await page.wait_for_selector(".LessonList-item", state="attached", timeout=60000)
    except PlaywrightTimeoutError:
        logger.warning("Timeout aguardando lista de aulas (.LessonList-item).")
    await page.wait_for_timeout(550)

    total_lessons = await stabilize_lesson_list_count(page, logger)
    logger.info(f"Matéria: {materia_name} | Aulas (lista estabilizada): {total_lessons}")

    pdf_config_type = CONFIG['pdfConfig'].get('pdfType', 2)
    if pdf_config_type == 4:
        types_to_download = [1, 2, 3]
        download_extras = True
    elif pdf_config_type == 5:
        types_to_download = [1, 3]
        download_extras = False
    else:
        types_to_download = [pdf_config_type]
        download_extras = False

    lesson_idx = 0
    while lesson_idx < total_lessons:
        if page.is_closed():
            raise BrowserSessionDeadError(
                "O Chromium encerrou ou a aba foi fechada durante o curso (PDF)."
            )

        if not _page_is_course_aulas_root_listing(page, course_url):
            await page_goto_resilient(
                page, course_url, logger, wait_until="domcontentloaded", timeout_ms=120000
            )
            if "app/dashboard/cursos" in page.url and not re.search(
                r"/cursos/\d+/aulas", page.url
            ):
                await page_goto_resilient(
                    page, course_url, logger, wait_until="domcontentloaded", timeout_ms=120000
                )
            try:
                await page.wait_for_selector(
                    ".LessonList-item", state="attached", timeout=45000
                )
            except PlaywrightTimeoutError:
                logger.warning("Lista de aulas não apareceu após voltar ao curso (PDF).")
            await page.wait_for_timeout(450)

        await scroll_lesson_index_into_view(page, lesson_idx, logger)
        await page.wait_for_timeout(180)

        items = page.locator(".LessonList-item")
        aula_element = items.nth(lesson_idx)
        if "isDisabled" in (await aula_element.get_attribute("class") or ""):
            logger.info(
                f"Pulando aula desabilitada ({lesson_folder_label_from_index(lesson_idx)})."
            )
            lesson_idx += 1
            continue

        aula_id = await aula_element.get_attribute("id") or f"idx{lesson_idx}"

        try:
            lesson_name_raw = await aula_element.locator(
                ".LessonCollapseHeader-title .SectionTitle"
            ).text_content(timeout=5000)
        except Exception:
            try:
                lesson_name_raw = await aula_element.locator(
                    "[class*='LessonCollapseHeader'] [class*='SectionTitle']"
                ).first.text_content(timeout=4000)
            except Exception:
                lesson_name_raw = lesson_folder_label_from_index(lesson_idx)

        try:
            lesson_subtitle_raw = await aula_element.locator(
                ".LessonCollapseHeader-title .sc-gZMcBi"
            ).text_content(timeout=4000)
        except Exception:
            lesson_subtitle_raw = "Geral"

        lesson_name = sanitize_filename((lesson_name_raw or "").strip())
        lesson_subtitle = sanitize_filename((lesson_subtitle_raw or "").strip())

        folder_label = lesson_folder_label_from_index(lesson_idx)
        lesson_dir = course_dir / folder_label
        lesson_dir.mkdir(parents=True, exist_ok=True)

        await force_expand_lesson_panel(page, aula_element, aula_id, logger)
        await page.wait_for_timeout(220)

        descriptive_stem = f"{lesson_name} - {lesson_subtitle}".strip(" -")

        # 1. PROCESSAR LIVROS (vários <a> por variante; subtítulo pode estar só no link)
        livro_selector = (
            'a:has-text("Baixar Livro Eletrônico"), '
            'a:has-text("versão simplificada"), '
            'a:has-text("marcação dos aprovados"), '
            'a[href*="pdfSimplificado"], a[href*="pdfGrifado"], a[href*="/pdf/download"], '
            'a[href*="pdf-simplificado"], a[href*="pdf-grifado"]'
        )
        raw_livro_anchors = await aula_element.locator(livro_selector).all()
        seen_pdf_urls = set()
        livro_jobs = []
        for button in raw_livro_anchors:
            pdf_url = await button.get_attribute('href')
            if not pdf_url:
                continue
            p = pdf_url.strip()
            if not p or p == '#' or p.lower().startswith('javascript:'):
                continue
            full_pdf_url = (
                "https://www.estrategiaconcursos.com.br" + pdf_url
                if pdf_url.startswith('/api')
                else pdf_url
            )
            t_detected = livro_pdf_type_from_url(full_pdf_url)
            if not t_detected:
                continue
            if full_pdf_url in seen_pdf_urls:
                continue
            seen_pdf_urls.add(full_pdf_url)
            if t_detected not in types_to_download:
                continue
            t_info = PDF_TYPES_CONFIG[t_detected]
            file_path = build_pdf_save_path(
                course_dir, folder_label, descriptive_stem, t_info["name"]
            )
            livro_jobs.append((button, full_pdf_url, file_path))

        await _download_pdf_jobs_parallel_get_then_click(
            page, livro_jobs, logger, progress
        )

        # 2. PROCESSAR MATERIAIS EXTRAS
        if download_extras:
            extra_jobs = []
            for text_key, suffix in EXTRA_MATERIALS.items():
                extra_buttons = await aula_element.locator(f'a:has-text("{text_key}")').all()
                for button in extra_buttons:
                    url = await button.get_attribute('href')
                    if not url:
                        continue
                    full_url = (
                        "https://www.estrategiaconcursos.com.br" + url
                        if url.startswith('/api')
                        else url
                    )
                    file_path = build_pdf_save_path(
                        course_dir, folder_label, descriptive_stem, suffix
                    )
                    extra_jobs.append((button, full_url, file_path))
            await _download_pdf_jobs_parallel_get_then_click(
                page, extra_jobs, logger, progress
            )

        lesson_idx += 1


async def _download_pdf_jobs_parallel_get_then_click(
    page,
    jobs: List[Tuple[Any, str, Path]],
    logger: logging.Logger,
    progress: dict,
) -> None:
    """
    Por aula: dispara vários GET de PDF em paralelo (simplificado + marcado, etc.);
    o que não vier por API segue com o fluxo de clique (sequencial), sem repetir GET.
    """
    if not jobs:
        return
    for _, _, fp in jobs:
        logger.info(f"Baixando PDF: {fp.name}")

    async def try_get_only(entry):
        button, url, file_path = entry
        progress_key = f"{file_path.name}-{url[-20:]}"
        try:
            if progress.get(progress_key) and file_path.exists():
                return (entry, False)
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_data, last_http = await _playwright_get_pdf_bytes(page, url, logger)
            if file_data:
                with open(file_path, "wb") as f:
                    f.write(file_data)
                await verify_download(file_path, logger)
                progress[progress_key] = True
                logger.info(f"SUCESSO: {file_path.name} salvo.")
                return (entry, False)
            if last_http is not None and last_http != 200:
                logger.warning(
                    f"GET PDF HTTP {last_http} para {url[:100]}… — tentando clique no link."
                )
            return (entry, True)
        except Exception as e:
            if playwright_session_dead(e):
                raise BrowserSessionDeadError(str(e)) from e
            logger.warning(
                f"GET paralelo falhou ({file_path.name}): {e} — tentará clique."
            )
            return (entry, True)

    results = await asyncio.gather(
        *[try_get_only(j) for j in jobs], return_exceptions=True
    )
    await save_progress(progress)
    for r in results:
        if isinstance(r, Exception):
            if isinstance(r, BrowserSessionDeadError):
                raise r
            logger.error(f"Erro em lote de PDFs: {r}")
            continue
        entry, need_click = r
        if need_click:
            b, u, p = entry
            await download_pdf_resource(
                page, u, p, logger, progress, b, skip_network_get=True
            )


async def _playwright_get_pdf_bytes(
    page, url: str, logger: logging.Logger, attempts: int = 6
) -> Tuple[Optional[bytes], Optional[int]]:
    """
    GET da URL do PDF com retentativas: 502/503/504/429 da API, ECONNRESET ao seguir
    redirect para a CDN, timeouts, etc. Retorna (corpo %PDF ou None, último HTTP status).
    """
    last_status: Optional[int] = None
    for attempt in range(1, attempts + 1):
        try:
            response = await page.request.get(url, timeout=120000)
            last_status = response.status
            if response.status in (502, 503, 504, 429):
                delay = min(40, 5 + attempt * 5)
                logger.warning(
                    f"GET PDF HTTP {response.status} ({attempt}/{attempts}); "
                    f"aguardando {delay}s (API/CDN instável?)…"
                )
                await asyncio.sleep(delay)
                continue
            if response.status != 200:
                return None, last_status
            file_data = await response.body()
            if len(file_data) >= 4 and file_data[:4] == b"%PDF":
                return file_data, 200
            logger.warning(
                f"GET 200 mas corpo não é PDF ({len(file_data)} b); "
                f"Content-Type: {response.headers.get('content-type', '?')}"
            )
            return None, last_status
        except Exception as e:
            if playwright_session_dead(e):
                raise BrowserSessionDeadError(str(e)) from e
            last_status = None
            msg = str(e).lower()
            retryable = any(
                x in msg
                for x in (
                    "econnreset",
                    "etimedout",
                    "timeout",
                    "timed out",
                    "broken pipe",
                    "connection reset",
                    "errno 104",
                    "err_connection",
                    "network",
                    "503",
                    "502",
                    "504",
                )
            )
            if not retryable or attempt >= attempts:
                logger.warning(f"GET PDF abortado após {attempt} tentativa(s): {e}")
                return None, last_status
            delay = min(35, 3 * attempt)
            logger.warning(
                f"GET PDF rede/transporte ({attempt}/{attempts}): {type(e).__name__}; "
                f"nova tentativa em {delay}s…"
            )
            await asyncio.sleep(delay)
    return None, last_status


async def download_pdf_resource(
    page,
    url,
    file_path,
    logger,
    progress,
    button_element=None,
    *,
    skip_network_get: bool = False,
):
    progress_key = f'{file_path.name}-{url[-20:]}'
    
    if progress.get(progress_key) and file_path.exists():
        return

    logger.info(f'Baixando PDF: {file_path.name}')
    file_path.parent.mkdir(parents=True, exist_ok=True)
    
    if not skip_network_get:
        file_data, last_http = await _playwright_get_pdf_bytes(page, url, logger)
        if file_data:
            with open(file_path, 'wb') as f:
                f.write(file_data)
            await verify_download(file_path, logger)
            progress[progress_key] = True
            await save_progress(progress)
            logger.info(f'SUCESSO: {file_path.name} salvo.')
            return
        if last_http is not None and last_http != 200:
            logger.warning(
                f"GET PDF HTTP {last_http} para {url[:100]}… — tentando clique no link."
            )
    elif not button_element:
        logger.error(f"Sem link para clique e GET desligado: {file_path.name}")
        return

    # Fallback: Clique (às vezes o site abre stream na aba; 2 tentativas, timeout maior)
    if button_element:
        for click_try in range(1, 3):
            try:
                await button_element.evaluate("node => node.setAttribute('target', '_self')")
                async with page.expect_download(timeout=120000) as download_info:
                    await button_element.click(force=True)
                download = await download_info.value
                await download.save_as(file_path)
                await verify_download(file_path, logger)
                progress[progress_key] = True
                await save_progress(progress)
                logger.info(f'SUCESSO (Clique): {file_path.name} salvo.')
                if "download" not in page.url and "cursos" not in page.url:
                    await page.go_back()
                return
            except Exception as e2:
                if click_try >= 2:
                    logger.error(f'Falha total em {file_path.name}: {e2}')
                else:
                    logger.warning(
                        f"Clique não disparou download ({type(e2).__name__}); "
                        f"nova tentativa em 4s ({click_try}/2)…"
                    )
                    await asyncio.sleep(4)


def _normalize_video_href(href: str) -> str:
    href = (href or "").strip()
    if not href:
        return ""
    if href.startswith("/"):
        return "https://www.estrategiaconcursos.com.br" + href.split("#")[0]
    return href.split("#")[0]


def _href_looks_like_lesson_video_link(href: str) -> bool:
    """
    Link para página de aula/player (path após /aulas/...), não só a listagem .../aulas.
    Ignora fórum e seções fora do fluxo de aulas.
    """
    if not href or ESTRATEGIA_HOST not in href.lower():
        return False
    low = href.lower()
    if "forum" in low or "/forum" in low or "discuss" in low:
        return False
    if "/api/" in low and "pdf" in low:
        return False
    if re.search(r"/cursos/\d+/aulas/.", low):
        return True
    if "videoteca" in low or "/video/" in low or "player" in low:
        return True
    return False


def _page_is_course_aulas_root_listing(page, course_url: str) -> bool:
    """True se estamos na página índice .../cursos/ID/aulas (lista), mesmo curso — não em .../aulas/subrota."""
    u = page.url.split("#")[0]
    c = course_url.split("#")[0]
    if "app/dashboard/cursos" not in u:
        return False
    m_c = re.search(r"/cursos/(\d+)/aulas", c)
    m_u = re.search(r"/cursos/(\d+)/aulas", u)
    if not m_c or not m_u or m_c.group(1) != m_u.group(1):
        return False
    tail = u[m_u.end() :].strip().lstrip("/")
    return tail == "" or tail.startswith("?")


def _css_id_attr_value(aula_id: str) -> str:
    """Valor seguro para seletor [id=\"...\"]."""
    return (aula_id or "").replace("\\", "\\\\").replace('"', '\\"')


def lesson_folder_label_from_index(lesson_idx: int) -> str:
    """Pasta fixa Aula 00, Aula 01, … (duas casas), ordem na lista do curso."""
    return f"Aula {lesson_idx:02d}"


async def stabilize_lesson_list_count(page, logger) -> int:
    """
    Lista de aulas costuma ser virtualizada: rola até o fim até o count estabilizar,
    para não subestimar o total (bug que encerrava o curso após a 1ª aula).
    """
    last_n = -1
    stable = 0
    for _ in range(45):
        n = await page.locator(".LessonList-item").count()
        if n <= 0:
            await page.wait_for_timeout(400)
            continue
        if n == last_n:
            stable += 1
            if stable >= 2:
                logger.info(f"Lista de aulas estabilizada em {n} item(ns).")
                return n
        else:
            stable = 0
        last_n = n
        try:
            await page.evaluate(
                """() => {
                    const items = document.querySelectorAll('.LessonList-item');
                    const last = items[items.length - 1];
                    if (last) last.scrollIntoView({ block: 'end', behavior: 'instant' });
                }"""
            )
        except Exception:
            pass
        await page.wait_for_timeout(320)
    logger.warning(f"Lista de aulas não estabilizou; usando último count={last_n}.")
    return max(last_n, 0)


async def scroll_lesson_index_into_view(page, lesson_idx: int, logger) -> None:
    """Garante que o .LessonList-item do índice exista no viewport (listas virtualizadas)."""
    n = 0
    for _attempt in range(28):
        try:
            await page.evaluate(
                """(idx) => {
                    const items = document.querySelectorAll('.LessonList-item');
                    const el = items[idx];
                    if (el) el.scrollIntoView({ block: 'center', behavior: 'instant' });
                }""",
                lesson_idx,
            )
        except Exception as ex:
            logger.debug(f"scroll_lesson_index_into_view: {ex}")
        await page.wait_for_timeout(300)
        n = await page.locator(".LessonList-item").count()
        if n > lesson_idx:
            return
    logger.warning(
        f"Ainda poucos itens no DOM (count={n}) para índice {lesson_idx}; seguindo mesmo assim."
    )


async def wait_for_video_player_ready(page, logger) -> None:
    """Player React pode demorar; tenta attached → visible com várias passadas."""
    last_err: Optional[Exception] = None
    for attempt in range(1, 5):
        try:
            await page.wait_for_selector(
                "video.video-react-video",
                state="attached",
                timeout=18000,
            )
            await page.wait_for_selector(
                "video.video-react-video",
                state="visible",
                timeout=50000,
            )
            await page.wait_for_timeout(600)
            return
        except Exception as e:
            last_err = e
            logger.warning(
                f"Aguardando player de vídeo (tentativa {attempt}/4): {e}"
            )
            try:
                await page.evaluate(
                    """() => {
                        const v = document.querySelector('video.video-react-video');
                        if (v) { try { v.play(); } catch (e) {} }
                    }"""
                )
            except Exception:
                pass
            await page.wait_for_timeout(1800)
    if last_err:
        raise last_err


async def force_expand_lesson_panel(
    page, aula_element, aula_id: str, logger
) -> None:
    """
    Abre o accordion da aula — alinhado ao fluxo de PDF:
    clique em .Collapse-header e espera itens internos (#aula_id … List-item / ListVideos).
    """
    await aula_element.scroll_into_view_if_needed()
    await page.wait_for_timeout(350)

    header = aula_element.locator(".Collapse-header").first
    if await header.count() > 0:
        try:
            await header.scroll_into_view_if_needed()
            await header.click(timeout=9000, force=True)
        except Exception as ex:
            logger.debug(f".Collapse-header: {ex}")

    id_attr = _css_id_attr_value(aula_id)
    if aula_id and not str(aula_id).startswith("idx"):
        sel_inner = (
            f'[id="{id_attr}"] [class*="List-item"], '
            f'[id="{id_attr}"] [class*="ListVideos"], '
            f'[id="{id_attr}"] a[href*="/cursos/"][href*="/aulas/"]'
        )
        try:
            await page.wait_for_selector(sel_inner, state="visible", timeout=14000)
        except Exception:
            try:
                await header.click(timeout=6000, force=True)
                await page.wait_for_selector(sel_inner, state="visible", timeout=8000)
            except Exception:
                logger.debug(f"Itens internos da aula id={aula_id!r} não apareceram a tempo.")

    header_selectors = [
        "a.Collapse-header",
        "div.Collapse-header",
        "[class*='Collapse-header']",
        ".LessonCollapseHeader-title",
    ]
    list_visible = aula_element.locator(
        "[class*='ListVideos'], [class*='List-item'], .ListVideos-items, [class*='ListVideos-items']"
    ).first
    for sel in header_selectors:
        h = aula_element.locator(sel).first
        if await h.count() == 0:
            continue
        try:
            await h.scroll_into_view_if_needed()
            for _ in range(2):
                try:
                    if await list_visible.is_visible(timeout=700):
                        return
                except Exception:
                    pass
                await h.click(timeout=7000, force=True)
                await page.wait_for_timeout(700)
        except Exception as ex:
            logger.debug(f"Expandir painel ({sel}): {ex}")
    try:
        await aula_element.evaluate(
            """(row) => {
                const h = row.querySelector(
                    '.Collapse-header, a.Collapse-header, [class*="Collapse-header"]'
                );
                if (h) {
                    h.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true }));
                }
            }"""
        )
        await page.wait_for_timeout(900)
    except Exception:
        pass


async def collect_video_entries_from_lesson_index(
    page, lesson_idx: int, logger
) -> List[Dict[str, str]]:
    """
    Fallback: lê o n-ésimo .LessonList-item no documento e extrai links de vídeo/aula.
    Evita stale locator quando o React troca classes internas.
    """
    try:
        raw_list = await page.evaluate(
            r"""(idx) => {
                const rows = Array.from(document.querySelectorAll('.LessonList-item'));
                const row = rows[idx];
                if (!row) return [];
                const out = [];
                const seen = new Set();
                function norm(h) {
                    if (!h) return '';
                    h = h.trim();
                    if (h.startsWith('/'))
                        return 'https://www.estrategiaconcursos.com.br' + h.split('#')[0];
                    return h.split('#')[0];
                }
                function titleFrom(a) {
                    const z = a.querySelector(
                        '[class*="VideoItem-info-title"], [class*="info-title"], [class*="title"]'
                    );
                    const t = z ? (z.textContent || '') : (a.innerText || '');
                    const line = (t || '').trim().split(/\r?\n/)[0].slice(0, 240);
                    return line || 'Video';
                }
                function okVideoHref(full) {
                    const low = full.toLowerCase();
                    if (low.indexOf('estrategiaconcursos.com.br') < 0) return false;
                    if (low.indexOf('forum') >= 0 || low.indexOf('/forum') >= 0) return false;
                    if (low.indexOf('/api/') >= 0 && low.indexOf('pdf') >= 0) return false;
                    return /\/cursos\/\d+\/aulas\/.+/.test(low);
                }
                row.querySelectorAll('a[href]').forEach((a) => {
                    const full = norm(a.getAttribute('href') || '');
                    if (!full || seen.has(full) || !okVideoHref(full)) return;
                    seen.add(full);
                    out.push({ href: full, title: titleFrom(a) });
                });
                return out;
            }""",
            lesson_idx,
        )
    except Exception as ex:
        logger.debug(f"collect_video_entries_from_lesson_index: {ex}")
        raw_list = []

    entries: List[Dict[str, str]] = []
    seen: set[str] = set()
    for item in raw_list or []:
        if not isinstance(item, dict):
            continue
        h = _normalize_video_href(str(item.get("href") or ""))
        if not h or h in seen or not _href_looks_like_lesson_video_link(h):
            continue
        seen.add(h)
        t = str(item.get("title") or "Video").strip().split("\n")[0].strip() or "Video"
        entries.append({"href": h, "title": t})
    if entries:
        logger.info(f"  > Vídeos detectados (fallback índice DOM #{lesson_idx}): {len(entries)}")
    return entries


async def collect_video_entries_for_lesson(
    aula_element, page, logger, aula_id: str, lesson_idx: int
) -> List[Dict[str, str]]:
    """
    Coleta {href, title} por aula. Tenta locators Playwright e depois JS no subárvore da linha.
    """
    entries: List[Dict[str, str]] = []
    seen: set[str] = set()

    async def _append_from_locator(loc) -> bool:
        try:
            n = await loc.count()
        except Exception:
            return False
        got = False
        for i in range(n):
            link = loc.nth(i)
            try:
                raw = await link.get_attribute("href")
            except Exception:
                continue
            full = _normalize_video_href(raw or "")
            if not full or full in seen or not _href_looks_like_lesson_video_link(full):
                continue
            seen.add(full)
            got = True
            try:
                tloc = link.locator(
                    ".VideoItem-info-title, [class*='VideoItem-info-title'], [class*='info-title']"
                ).first
                if await tloc.count() > 0:
                    title = (await tloc.text_content(timeout=2500) or "").strip()
                else:
                    title = ""
            except Exception:
                title = ""
            title = title.split("\n")[0].strip() if title else ""
            if not title:
                title = "Video"
            entries.append({"href": full, "title": title})
        return got

    if aula_id and not str(aula_id).startswith("idx"):
        id_attr = _css_id_attr_value(aula_id)
        inner = page.locator(f'[id="{id_attr}"] a[href]')
        if await _append_from_locator(inner):
            logger.info(f"  > Vídeos detectados (container #{aula_id}): {len(entries)}")
            return entries

    playwright_selectors = [
        '[class*="List-item"] a[href]',
        'a[href*="/cursos/"][href*="/aulas/"]',
        ".ListVideos-items-video a.VideoItem",
        ".ListVideos-items a.VideoItem",
        "div[class*='ListVideos'] a[href]",
        "a.VideoItem[href]",
        "a[class*='VideoItem'][href]",
    ]
    for sel in playwright_selectors:
        loc = aula_element.locator(sel)
        if await _append_from_locator(loc):
            logger.info(f"  > Vídeos detectados (locator …{sel[-44:]}): {len(entries)}")
            return entries

    try:
        raw_list = await aula_element.evaluate(
            r"""(row) => {
                const out = [];
                const seen = new Set();
                function norm(h) {
                    if (!h) return '';
                    h = h.trim();
                    if (h.startsWith('/'))
                        return 'https://www.estrategiaconcursos.com.br' + h.split('#')[0];
                    return h.split('#')[0];
                }
                function titleFrom(a) {
                    const z = a.querySelector(
                        '[class*="VideoItem-info-title"], [class*="info-title"]'
                    );
                    const t = z ? (z.textContent || '') : (a.innerText || '');
                    const line = (t || '').trim().split(/\r?\n/)[0].slice(0, 240);
                    return line || 'Video';
                }
                function okVideoHref(full) {
                    const low = full.toLowerCase();
                    if (low.indexOf('estrategiaconcursos.com.br') < 0) return false;
                    if (low.indexOf('forum') >= 0 || low.indexOf('/forum') >= 0) return false;
                    if (low.indexOf('/api/') >= 0 && low.indexOf('pdf') >= 0) return false;
                    return /\/cursos\/\d+\/aulas\/.+/.test(low);
                }
                const sels = [
                    '[class*="ListVideos"] a[href]',
                    '[class*="List-item"] a[href]',
                    'a.VideoItem[href]',
                    'a[class*="VideoItem"][href]',
                ];
                for (const sel of sels) {
                    row.querySelectorAll(sel).forEach((a) => {
                        const full = norm(a.getAttribute('href') || '');
                        if (!full || seen.has(full) || !okVideoHref(full)) return;
                        seen.add(full);
                        out.push({ href: full, title: titleFrom(a) });
                    });
                }
                if (out.length === 0) {
                    row.querySelectorAll('a[href]').forEach((a) => {
                        const full = norm(a.getAttribute('href') || '');
                        if (!full || seen.has(full) || !okVideoHref(full)) return;
                        seen.add(full);
                        out.push({ href: full, title: titleFrom(a) });
                    });
                }
                return out;
            }"""
        )
    except Exception as ex:
        logger.debug(f"Fallback JS vídeos: {ex}")
        raw_list = []

    for item in raw_list or []:
        if not isinstance(item, dict):
            continue
        h = _normalize_video_href(str(item.get("href") or ""))
        if not h or h in seen or not _href_looks_like_lesson_video_link(h):
            continue
        seen.add(h)
        t = str(item.get("title") or "Video").strip().split("\n")[0].strip() or "Video"
        entries.append({"href": h, "title": t})

    if entries:
        logger.info(f"  > Vídeos detectados (fallback JS na linha): {len(entries)}")
        return entries

    entries = await collect_video_entries_from_lesson_index(page, lesson_idx, logger)
    return entries


async def process_course_video(page, course_url, progress, logger):
    """
    Baixa vídeos por matéria, em sequência estrita: congela o total de aulas, pasta ``Aula 00``, ``Aula 01``, …
    Só avança para a próxima aula após verificar o disco; só então passa para a próxima matéria (no fluxo global).
    """
    BASE_DIR = Path(CONFIG['videoConfig']['pastaDownloads'])
    logger.info(f"Abrindo curso (Vídeo): {course_url}")

    await page_goto_resilient(
        page, course_url, logger, wait_until="domcontentloaded", timeout_ms=120000
    )
    if "app/dashboard/cursos" in page.url and not re.search(r'/cursos/\d+/aulas', page.url):
        await page_goto_resilient(
            page, course_url, logger, wait_until="domcontentloaded", timeout_ms=120000
        )
    try:
        await page.wait_for_load_state("networkidle", timeout=25000)
    except Exception:
        pass

    try:
        course_name_element = await page.wait_for_selector('.CourseInfo-content-title', timeout=30000)
        course_name = await course_name_element.text_content()
    except PlaywrightTimeoutError:
        course_name = await page.title()

    materia_name = extract_materia_name(course_name)
    course_dir = BASE_DIR / sanitize_filename(materia_name)
    course_dir.mkdir(parents=True, exist_ok=True)

    try:
        await page.wait_for_selector(
            ".LessonList-item", state="attached", timeout=60000
        )
    except PlaywrightTimeoutError:
        logger.warning("Timeout aguardando lista de aulas (.LessonList-item).")
    await page.wait_for_timeout(2000)

    total_lessons = await stabilize_lesson_list_count(page, logger)
    logger.info(
        f"Matéria: {materia_name} | Total de aulas (sequência congelada): {total_lessons}"
    )

    user_agent = await page.evaluate("navigator.userAgent")
    videos_found_in_course = False
    same_lesson_incomplete_rounds = 0

    lesson_idx = 0
    while lesson_idx < total_lessons:
        if page.is_closed():
            raise BrowserSessionDeadError(
                "O Chromium encerrou ou a aba foi fechada durante o curso."
            )
        if not _page_is_course_aulas_root_listing(page, course_url):
            await page_goto_resilient(
                page, course_url, logger, wait_until="domcontentloaded", timeout_ms=120000
            )
            if "app/dashboard/cursos" in page.url and not re.search(
                r"/cursos/\d+/aulas", page.url
            ):
                await page_goto_resilient(
                    page, course_url, logger, wait_until="domcontentloaded", timeout_ms=120000
                )
            try:
                await page.wait_for_selector(
                    ".LessonList-item", state="attached", timeout=45000
                )
            except PlaywrightTimeoutError:
                logger.warning("Lista de aulas não apareceu após voltar ao curso.")
            await page.wait_for_timeout(900)

        await scroll_lesson_index_into_view(page, lesson_idx, logger)
        await page.wait_for_timeout(350)

        items = page.locator(".LessonList-item")
        aula_element = items.nth(lesson_idx)
        if "isDisabled" in (await aula_element.get_attribute("class") or ""):
            logger.info(
                f"Pulando aula desabilitada ({lesson_folder_label_from_index(lesson_idx)})."
            )
            lesson_idx += 1
            same_lesson_incomplete_rounds = 0
            continue

        aula_id = await aula_element.get_attribute("id") or f"idx{lesson_idx}"
        try:
            lesson_name_raw = await aula_element.locator(
                ".LessonCollapseHeader-title .SectionTitle"
            ).text_content(timeout=5000)
        except Exception:
            try:
                lesson_name_raw = await aula_element.locator(
                    "[class*='LessonCollapseHeader'] [class*='SectionTitle']"
                ).first.text_content(timeout=4000)
            except Exception:
                lesson_name_raw = lesson_folder_label_from_index(lesson_idx)

        lesson_folder = lesson_folder_label_from_index(lesson_idx)
        lesson_name = sanitize_filename(lesson_folder)

        try:
            logger.info(
                f"--- {lesson_folder} ({lesson_idx + 1}/{total_lessons}) — site: {lesson_name_raw.strip()[:120]} ---"
            )

            await force_expand_lesson_panel(page, aula_element, aula_id, logger)
            await page.wait_for_timeout(800)
            video_entries = await collect_video_entries_for_lesson(
                aula_element, page, logger, aula_id, lesson_idx
            )
            n_videos = len(video_entries)
            if n_videos == 0:
                logger.warning(
                    f"  > Nenhum link de vídeo em {lesson_folder} (após expandir). Avançando."
                )
                lesson_idx += 1
                same_lesson_incomplete_rounds = 0
                continue

            videos_found_in_course = True
            lesson_dir = course_dir / lesson_name
            lesson_dir.mkdir(parents=True, exist_ok=True)

            n_disk = count_complete_video_slots(lesson_dir, lesson_name, n_videos)
            if n_disk >= n_videos:
                logger.info(
                    f"{lesson_folder} já completa no disco ({n_disk}/{n_videos}). "
                    "Verificação OK; próxima aula."
                )
                lesson_idx += 1
                same_lesson_incomplete_rounds = 0
                continue

            if n_disk > 0:
                logger.info(
                    f"{lesson_folder} parcial no disco ({n_disk}/{n_videos}); "
                    "baixando só o que faltar."
                )

            for j, entry in enumerate(video_entries, 1):
                href = entry.get("href") or ""
                raw_title = entry.get("title") or f"Video_{j}"
                video_title = sanitize_filename(raw_title)
                progress_key = f"{aula_id}-{video_title}-{j}"

                existing_file = find_complete_video_for_lesson_slot(
                    lesson_dir, lesson_name, j, video_title
                )
                if existing_file is None:
                    existing_file = find_complete_video_slot_without_title(
                        lesson_dir, lesson_name, j
                    )

                if existing_file is not None:
                    progress[progress_key] = True
                    await save_progress(progress)
                    logger.info(
                        f'Vídeo já OK no disco: "{video_title}" ({existing_file.name})'
                    )
                    await asyncio.sleep(0.3)
                    continue

                if progress.get(progress_key):
                    progress.pop(progress_key, None)
                    await save_progress(progress)
                    logger.info(
                        f'Chave de progresso órfã (sem .mp4 no slot {j}); baixando: "{video_title}"'
                    )

                try:
                    remove_incomplete_mp4_for_lesson_slot(
                        lesson_dir, lesson_name, j, logger
                    )
                    await page_goto_resilient(
                        page,
                        href,
                        logger,
                        wait_until="domcontentloaded",
                        timeout_ms=120000,
                    )
                    await page.wait_for_timeout(1200)
                    await wait_for_video_player_ready(page, logger)

                    video_info = await get_video_url_by_resolution(
                        page, CONFIG["videoConfig"]["resolucaoEscolhida"], logger
                    )
                    video_url = video_info["url"]
                    used_resolution = video_info["resolution"]

                    if not video_url:
                        raise RuntimeError("Falha ao obter URL do vídeo.")

                    file_name = build_safe_video_filename(
                        lesson_dir,
                        lesson_name,
                        j,
                        video_title,
                        used_resolution,
                    )
                    file_path = lesson_dir / file_name

                    browser_cookies = await page.context.cookies()
                    aiohttp_cookies = {
                        c["name"]: c["value"] for c in browser_cookies
                    }
                    headers = {"Referer": page.url, "User-Agent": user_agent}

                    logger.info(f"Baixando: {file_name}")
                    await download_file_aiohttp(
                        video_url,
                        file_path,
                        logger,
                        cookies=aiohttp_cookies,
                        headers=headers,
                    )
                    await verify_download(file_path, logger)
                    progress[progress_key] = True
                    await save_progress(progress)
                    await asyncio.sleep(1.5)

                except BrowserSessionDeadError:
                    raise
                except Exception as e:
                    if playwright_session_dead(e):
                        raise BrowserSessionDeadError(str(e)) from e
                    logger.error(f'Erro no vídeo "{video_title}": {e}')
                    if not page.is_closed():
                        try:
                            await page_goto_resilient(
                                page,
                                course_url,
                                logger,
                                wait_until="domcontentloaded",
                                timeout_ms=120000,
                            )
                        except BrowserSessionDeadError:
                            raise
                        except Exception as nav_e:
                            logger.error(f"Volta ao curso após erro: {nav_e}")

            await page_goto_resilient(
                page,
                course_url,
                logger,
                wait_until="domcontentloaded",
                timeout_ms=120000,
            )
            await page.wait_for_timeout(1200)

            n_ok_final = count_complete_video_slots(
                lesson_dir, lesson_name, n_videos
            )
            if n_ok_final < n_videos:
                same_lesson_incomplete_rounds += 1
                logger.warning(
                    f"{lesson_folder} ainda incompleta no disco ({n_ok_final}/{n_videos}). "
                    f"Tentativa de refazer a mesma aula: {same_lesson_incomplete_rounds}/8."
                )
                if same_lesson_incomplete_rounds >= 8:
                    logger.error(
                        f"Muitas falhas em {lesson_folder}; avançando para evitar loop infinito."
                    )
                    lesson_idx += 1
                    same_lesson_incomplete_rounds = 0
                continue

            same_lesson_incomplete_rounds = 0
            logger.info(
                f"Concluído {lesson_folder}: {n_ok_final}/{n_videos} vídeo(s) verificados no disco. "
                "Próxima aula."
            )
            lesson_idx += 1

        except BrowserSessionDeadError:
            raise
        except Exception as e:
            if playwright_session_dead(e):
                raise BrowserSessionDeadError(str(e)) from e
            logger.error(f"Erro na aula {lesson_folder}: {e}")
            lesson_idx += 1
            same_lesson_incomplete_rounds = 0

    if not videos_found_in_course:
        logger.warning(
            "Nenhuma aula deste curso teve links de vídeo detectados "
            "(DOM pode ter mudado ou a lista não carregou)."
        )

async def download_logic_main(progress_callback, log_queue):
    global CONFIG
    try:
        with open("config.json", "r", encoding="utf-8") as f:
            CONFIG = json.load(f)
    except Exception:
        pass

    logger = logging.getLogger(__name__)
    logger.setLevel(logging.INFO)
    if not logger.handlers:
        queue_handler = QueueHandler(log_queue)
        formatter = logging.Formatter('[%(asctime)s] %(levelname)s: %(message)s', datefmt='%H:%M:%S')
        queue_handler.setFormatter(formatter)
        logger.addHandler(queue_handler)
    sys.stdout = PrintRedirector(logger)
    
    logger.info("Iniciando processo...")
    
    with open('course-urls.json', 'r', encoding='utf-8') as f:
        current_course_urls = json.load(f)
    
    total_cursos = len(current_course_urls)
    if total_cursos == 0:
        logger.warning("Lista de cursos vazia.")
        return
        
    async with async_playwright() as p:
        browser_context = None
        try:
            logger.info("Iniciando navegador...")
            browser_context = await p.chromium.launch_persistent_context(
                user_data_dir=Path.home() / "AppData" / "Local" / "EstrategiaDownloaderCache",
                headless=CONFIG.get('headless', False),
                viewport=None, 
                args=[
                    '--start-maximized', 
                    '--no-sandbox', 
                    '--disable-setuid-sandbox',
                    '--disable-infobars',
                    '--disable-notifications'
                ]
            )
            
            login_page = await browser_context.new_page()
            
            await ensure_logged_in(login_page, logger)
            await apply_post_login_window_behavior(login_page, logger)
            # Chromium (contexto persistente) não tolera ficar com zero abas: new_page() falha com
            # Protocol error (Target.createTarget) se fecharmos a única aba antes dos workers.
            keeper_page = await browser_context.new_page()
            await keeper_page.goto("about:blank")
            await login_page.close()

            progress_data = await load_progress()
            
            download_type = CONFIG.get('downloadType', 'pdf')
            # PDF: várias matérias ao mesmo tempo (config: pdfConfig.concurrentMatriculas, 2–10, default 6).
            # Vídeo: um curso após o outro.
            pdf_cfg = CONFIG.get("pdfConfig") or {}
            sem_limit = (
                1
                if download_type == "video"
                else max(2, min(10, int(pdf_cfg.get("concurrentMatriculas", 6))))
            )
            semaphore = asyncio.Semaphore(sem_limit)

            if download_type == 'video':
                for i, course_url in enumerate(current_course_urls):
                    await download_manager(
                        i + 1,
                        total_cursos,
                        course_url,
                        browser_context,
                        progress_data,
                        logger,
                        semaphore,
                        progress_callback,
                    )
            else:
                tasks = []
                for i, course_url in enumerate(current_course_urls):
                    await asyncio.sleep(i * 0.2)
                    task = asyncio.create_task(
                        download_manager(
                            i + 1,
                            total_cursos,
                            course_url,
                            browser_context,
                            progress_data,
                            logger,
                            semaphore,
                            progress_callback,
                        )
                    )
                    tasks.append(task)
                await asyncio.gather(*tasks)

        except BrowserSessionDeadError:
            logger.error(
                "Sessão do navegador encerrada durante o processo (vídeo ou PDF)."
            )
        except Exception as e:
            logger.error(f"Erro geral: {e}", exc_info=True)
        finally:
            if browser_context:
                logger.info("Fechando navegador...")
                await browser_context.close()
                
    logger.info("--- FINALIZADO ---")

async def download_manager(curso_num, total_cursos, course_url, browser_context, progress_data, logger, semaphore, progress_callback):
    async with semaphore:
        page = None
        try:
            try:
                page = await browser_context.new_page()
            except Exception as e:
                if playwright_session_dead(e):
                    raise BrowserSessionDeadError(
                        "Não foi possível abrir aba: contexto/navegador já encerrado."
                    ) from e
                raise

            logger.info(f"--- Curso {curso_num}/{total_cursos} ---")

            download_type = CONFIG.get('downloadType', 'pdf')
            if download_type == 'pdf':
                await process_course_pdf(page, course_url, progress_data, logger)
            elif download_type == 'video':
                await process_course_video(page, course_url, progress_data, logger)

            progress_callback(curso_num / total_cursos)
        except BrowserSessionDeadError:
            raise
        except Exception as e:
            if playwright_session_dead(e):
                raise BrowserSessionDeadError(str(e)) from e
            logger.error(f"Erro curso: {e}")
        finally:
            if page:
                try:
                    if not page.is_closed():
                        await page.close()
                except Exception:
                    pass


async def scrape_matriculas_catalog_logic(log_queue) -> list[dict]:
    """
    Abre o site logado, percorre as páginas de 'Meus cursos' / assinaturas e
    devolve [{'titulo', 'url'}, ...] das matrículas encontradas.
    """
    global CONFIG
    try:
        with open("config.json", "r", encoding="utf-8") as f:
            CONFIG = json.load(f)
    except Exception:
        pass

    logger = logging.getLogger("matriculas")
    logger.setLevel(logging.INFO)
    if not logger.handlers:
        qh = QueueHandler(log_queue)
        qh.setFormatter(logging.Formatter("[%(asctime)s] %(levelname)s: %(message)s", datefmt="%H:%M:%S"))
        logger.addHandler(qh)

    merged: list[dict] = []
    seen_urls: set[str] = set()

    async with async_playwright() as p:
        context = None
        try:
            logger.info("Mapeando cursos em Minhas matrículas / Meus cursos...")
            context = await p.chromium.launch_persistent_context(
                user_data_dir=Path.home() / "AppData" / "Local" / "EstrategiaDownloaderCache",
                headless=CONFIG.get("headless", False),
                viewport=None,
                args=[
                    "--start-maximized",
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-infobars",
                ],
            )
            page = await context.new_page()
            await ensure_logged_in(page, logger)
            await apply_post_login_window_behavior(page, logger)

            for list_url in MINHAS_MATRICULAS_URLS:
                try:
                    logger.info(f"Abrindo: {list_url}")
                    await page.goto(list_url, wait_until="networkidle", timeout=120000)
                    await page.wait_for_selector(
                        'section[id^="card"], div.containerCursos, a[href*="/app/dashboard/cursos/"]',
                        timeout=35000,
                    )
                except PlaywrightTimeoutError:
                    logger.warning(f"Timeout ou layout diferente em: {list_url}")
                    continue
                except Exception as e:
                    logger.warning(f"Erro ao carregar {list_url}: {e}")
                    continue

                batch = await collect_courses_with_metadata(page, logger)
                for item in batch:
                    u = item.get("url") or ""
                    if u and u not in seen_urls:
                        seen_urls.add(u)
                        merged.append(item)

            if merged:
                try:
                    with open(CATALOGO_MAPEADO_FILE, "w", encoding="utf-8") as f:
                        json.dump(merged, f, indent=2, ensure_ascii=False)
                    logger.info(f"Catálogo salvo em {CATALOGO_MAPEADO_FILE.name} ({len(merged)} itens).")
                except OSError as e:
                    logger.error(f"Não foi possível salvar o catálogo: {e}")
            else:
                logger.error(
                    "Nenhum curso encontrado. Confirme se está logado e se a lista de cursos carrega no site."
                )

        except Exception as e:
            logger.error(f"Erro ao mapear matrículas: {e}", exc_info=True)
        finally:
            if context:
                await context.close()

    return merged


async def scrape_package_logic(package_url, log_queue, callback):
    global CONFIG
    try:
        with open("config.json", "r", encoding="utf-8") as f:
            CONFIG = json.load(f)
    except Exception:
        pass

    logger = logging.getLogger(__name__)
    logger.setLevel(logging.INFO)
    if not logger.handlers:
        queue_handler = QueueHandler(log_queue)
        formatter = logging.Formatter('[%(asctime)s] %(levelname)s: %(message)s', datefmt='%H:%M:%S')
        queue_handler.setFormatter(formatter)
        logger.addHandler(queue_handler)
    
    logger.info(f"Buscando pacote...")
    async with async_playwright() as p:
        context = None
        try:
            context = await p.chromium.launch_persistent_context(
                user_data_dir=Path.home() / "AppData" / "Local" / "EstrategiaDownloaderCache",
                headless=CONFIG.get('headless', False),
                viewport=None,
                args=['--start-maximized', '--no-sandbox', '--disable-setuid-sandbox', '--disable-infobars']
            )
            page = await context.new_page()
            
            await ensure_logged_in(page, logger)
            await apply_post_login_window_behavior(page, logger)

            await page.goto(package_url, wait_until='networkidle', timeout=60000)

            if re.search(r"/app/dashboard/cursos(?:/|\?|#|$)", package_url.lower()):
                try:
                    await page.wait_for_selector('section[id^="card"], div.containerCursos', timeout=45000)
                except PlaywrightTimeoutError:
                    logger.warning("Timeout aguardando cards de curso; tentando coletar links mesmo assim.")

            found_urls = await collect_course_urls_from_listing_page(page, logger)

            if not found_urls:
                logger.error(
                    "Nenhum link de curso encontrado nesta página. "
                    "Se for 'Meus cursos', deixe a lista carregar no navegador e tente de novo. "
                    f"URL atual: {page.url}"
                )
                return
            
            try:
                with open('course-urls.json', 'r', encoding='utf-8') as f:
                    current_urls = json.load(f)
            except: current_urls = []

            newly_added = 0
            for url in found_urls:
                if url not in current_urls:
                    current_urls.append(url)
                    newly_added += 1
            
            with open('course-urls.json', 'w', encoding='utf-8') as f:
                json.dump(current_urls, f, indent=2, ensure_ascii=False)
            
            logger.info(f"{newly_added} matérias adicionadas.")

        except Exception as e:
            logger.error(f"Erro no pacote: {e}")
        finally:
            if context: await context.close()
            if callback: callback()


class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("🦉 Estratégia Downloader Pro")
        self.geometry("1100x700")
        self.minsize(800, 600)
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")
        self.log_queue = queue.Queue()
        self.download_thread = None
        self._cached_matriculas: list = []
        self._matriculas_scan_running = False
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)
        self.nav_frame = ctk.CTkFrame(self, width=200, corner_radius=0)
        self.nav_frame.grid(row=0, column=0, sticky="nsew")
        self.nav_frame.grid_rowconfigure(7, weight=1) 
        
        self.logo_label = ctk.CTkLabel(self.nav_frame, text="Downloader Pro", font=ctk.CTkFont(size=20, weight="bold"))
        self.logo_label.grid(row=0, column=0, padx=20, pady=20)
        
        self.home_button = ctk.CTkButton(self.nav_frame, text="▶️ Início", command=self.show_home_frame)
        self.home_button.grid(row=1, column=0, padx=20, pady=10, sticky="ew")
        
        self.pacotes_button = ctk.CTkButton(self.nav_frame, text="📦 Pacotes", command=self.show_pacotes_frame)
        self.pacotes_button.grid(row=2, column=0, padx=20, pady=10, sticky="ew")

        self.matriculas_catalog_button = ctk.CTkButton(
            self.nav_frame, text="📋 Minhas matrículas", command=self.show_matriculas_catalog_frame
        )
        self.matriculas_catalog_button.grid(row=3, column=0, padx=20, pady=10, sticky="ew")
        
        self.materias_button = ctk.CTkButton(self.nav_frame, text="📚 Matérias Individuais", command=self.show_materias_frame)
        self.materias_button.grid(row=4, column=0, padx=20, pady=10, sticky="ew")

        self.logs_button = ctk.CTkButton(self.nav_frame, text="📄 Logs", command=self.show_logs_frame)
        self.logs_button.grid(row=5, column=0, padx=20, pady=10, sticky="ew")
        
        self.settings_button = ctk.CTkButton(self.nav_frame, text="⚙️ Configurações", command=self.show_settings_frame)
        self.settings_button.grid(row=6, column=0, padx=20, pady=10, sticky="ew")
        
        self.home_frame = self.create_home_frame()
        self.settings_frame = self.create_settings_frame()
        self.pacotes_frame = self.create_pacotes_frame()
        self.matriculas_catalog_frame = self.create_matriculas_catalog_frame()
        self.materias_frame = self.create_materias_frame()
        self.logs_frame = self.create_logs_frame()
        
        self.after(100, self.process_log_queue)
        self.show_home_frame()

    def select_frame(self, frame_to_show):
        self.home_frame.grid_forget()
        self.settings_frame.grid_forget()
        self.pacotes_frame.grid_forget()
        self.matriculas_catalog_frame.grid_forget()
        self.materias_frame.grid_forget()
        self.logs_frame.grid_forget()
        frame_to_show.grid(row=0, column=1, sticky="nsew", padx=20, pady=20)

    def show_home_frame(self): self.select_frame(self.home_frame)
    def show_settings_frame(self): self.load_settings_into_ui(); self.select_frame(self.settings_frame)
    def show_pacotes_frame(self): self.select_frame(self.pacotes_frame)
    def show_matriculas_catalog_frame(self):
        self._refresh_matriculas_catalog_from_disk()
        self.select_frame(self.matriculas_catalog_frame)

    def show_materias_frame(self): self.load_urls_into_ui(); self.select_frame(self.materias_frame)
    def show_logs_frame(self): self.select_frame(self.logs_frame)

    def create_home_frame(self):
        frame = ctk.CTkFrame(self)
        frame.grid_columnconfigure(0, weight=1)
        title = ctk.CTkLabel(frame, text="🦉 Downloader Pro – Painel Inicial", font=ctk.CTkFont(size=26, weight="bold"), text_color="#1E90FF")
        title.grid(row=0, column=0, padx=20, pady=(30, 10))
        self.start_button = ctk.CTkButton(frame, text="⏬ INICIAR DOWNLOADS", height=55, width=250, font=ctk.CTkFont(size=18, weight="bold"), fg_color="#00897B", hover_color="#00BFA5", text_color="white", corner_radius=8, command=self.start_download)
        self.start_button.grid(row=1, column=0, padx=20, pady=20)
        progress_label = ctk.CTkLabel(frame, text="📦 Progresso dos Downloads:", font=ctk.CTkFont(size=14), anchor="w")
        progress_label.grid(row=2, column=0, sticky="w", padx=50, pady=(10, 5))
        self.progress_bar = ctk.CTkProgressBar(frame, height=15)
        self.progress_bar.set(0)
        self.progress_bar.grid(row=3, column=0, padx=50, pady=(0, 20), sticky="ew")
        return frame

    def create_logs_frame(self):
        frame = ctk.CTkFrame(self)
        frame.grid_rowconfigure(1, weight=1)
        frame.grid_columnconfigure(0, weight=1)
        title = ctk.CTkLabel(frame, text="📝 Log do Processo", font=ctk.CTkFont(size=18, weight="bold"), text_color="#42A5F5")
        title.grid(row=0, column=0, sticky="w", padx=20, pady=(20, 10))
        self.log_textbox = ctk.CTkTextbox(frame, state="disabled", wrap="word", font=("Courier New", 12), text_color="#E0E0E0", fg_color="#101010")
        self.log_textbox.grid(row=1, column=0, sticky="nsew", padx=20, pady=(0, 20))
        return frame

    def create_settings_frame(self):
        settings_frame = ctk.CTkScrollableFrame(self, label_text="🎛️ Painel de Configurações")
        settings_frame.grid_columnconfigure(0, weight=1)
        self.settings_widgets = {}

        def create_simple_setting_row(parent_frame, row, text, key, key_path=None, widget_type='entry', options=None):
            label = ctk.CTkLabel(parent_frame, text=text, anchor="w")
            label.grid(row=row, column=0, padx=20, pady=12, sticky="w")
            widget = None
            if widget_type == 'entry': widget = ctk.CTkEntry(parent_frame, width=350)
            elif widget_type == 'password': widget = ctk.CTkEntry(parent_frame, width=350, show="*")
            elif widget_type == 'combo': widget = ctk.CTkComboBox(parent_frame, width=350, values=options if options else [])
            elif widget_type == 'switch': widget = ctk.CTkSwitch(parent_frame, text="")
            if widget:
                widget.grid(row=row, column=1, padx=20, pady=12, sticky="ew")
                self.settings_widgets[key] = (widget, key_path)

        def create_section_title(frame, text):
            title = ctk.CTkLabel(frame, text=text, font=ctk.CTkFont(size=18, weight="bold"), text_color="#1E90FF")
            title.grid(row=0, column=0, columnspan=2, padx=20, pady=(10, 5), sticky="w")
            underline = ctk.CTkFrame(frame, height=1, fg_color="#444")
            underline.grid(row=1, column=0, columnspan=2, sticky="ew", padx=20)

        general_group = ctk.CTkFrame(settings_frame, corner_radius=10, border_width=1, border_color="#444", fg_color="#1E1E1E")
        general_group.pack(fill="x", pady=(10, 15), ipady=10, padx=10)
        general_group.grid_columnconfigure(1, weight=1)
        create_section_title(general_group, "GERAL")
        create_simple_setting_row(general_group, 2, "Email:", "email")
        create_simple_setting_row(general_group, 3, "Senha:", "senha", widget_type='password')
        create_simple_setting_row(general_group, 4, "Tipo de Download:", "downloadType", widget_type='combo', options=['pdf', 'video'])
        create_simple_setting_row(general_group, 5, "Navegador Invisível (headless):", "headless", widget_type='switch')
        create_simple_setting_row(
            general_group,
            6,
            "Minimizar janela após login (desligado = você vê o navegador):",
            "minimizeAfterLogin",
            widget_type='switch',
        )

        pdf_group = ctk.CTkFrame(settings_frame, corner_radius=10, border_width=1, border_color="#444", fg_color="#1E1E1E")
        pdf_group.pack(fill="x", pady=15, ipady=10, padx=10)
        pdf_group.grid_columnconfigure(1, weight=1)
        create_section_title(pdf_group, "CONFIGURAÇÕES DE PDF")
        pdf_folder_label = ctk.CTkLabel(pdf_group, text="Pasta de PDFs:", anchor="w")
        pdf_folder_label.grid(row=2, column=0, padx=20, pady=12, sticky="w")
        pdf_input_frame = ctk.CTkFrame(pdf_group, fg_color="transparent")
        pdf_input_frame.grid(row=2, column=1, padx=20, pady=12, sticky="ew")
        pdf_input_frame.grid_columnconfigure(0, weight=1)
        pdf_folder_entry = ctk.CTkEntry(pdf_input_frame)
        pdf_folder_entry.grid(row=0, column=0, sticky="ew")
        pdf_folder_button = ctk.CTkButton(pdf_input_frame, text="🔍 Procurar...", width=100, fg_color="#00C853", hover_color="#00E676", text_color="black", command=lambda w=pdf_folder_entry: self.browse_folder(w))
        pdf_folder_button.grid(row=0, column=1, padx=(10, 0))
        self.settings_widgets["pastaDownloads_pdf"] = (pdf_folder_entry, ("pdfConfig",))
        create_simple_setting_row(
            pdf_group,
            3,
            "Tipo de PDF:",
            "pdfType",
            ("pdfConfig",),
            "combo",
            options=[
                "1: Simplificado",
                "2: Original",
                "3: Marcado",
                "4: Todos (+ extras)",
                "5: Simplificado + Marcado",
            ],
        )

        video_group = ctk.CTkFrame(settings_frame, corner_radius=10, border_width=1, border_color="#444", fg_color="#1E1E1E")
        video_group.pack(fill="x", pady=15, ipady=10, padx=10)
        video_group.grid_columnconfigure(1, weight=1)
        create_section_title(video_group, "CONFIGURAÇÕES DE VÍDEO")
        video_folder_label = ctk.CTkLabel(video_group, text="Pasta de Vídeos:", anchor="w")
        video_folder_label.grid(row=2, column=0, padx=20, pady=12, sticky="w")
        video_input_frame = ctk.CTkFrame(video_group, fg_color="transparent")
        video_input_frame.grid(row=2, column=1, padx=20, pady=12, sticky="ew")
        video_input_frame.grid_columnconfigure(0, weight=1)
        video_folder_entry = ctk.CTkEntry(video_input_frame)
        video_folder_entry.grid(row=0, column=0, sticky="ew")
        video_folder_button = ctk.CTkButton(video_input_frame, text="🔍 Procurar...", width=100, fg_color="#00C853", hover_color="#00E676", text_color="black", command=lambda w=video_folder_entry: self.browse_folder(w))
        video_folder_button.grid(row=0, column=1, padx=(10, 0))
        self.settings_widgets["pastaDownloads_video"] = (video_folder_entry, ("videoConfig",))
        create_simple_setting_row(video_group, 3, "Resolução de Vídeo:", "resolucaoEscolhida", ("videoConfig",), 'combo', options=['720p', '480p', '360p'])

        save_button = ctk.CTkButton(settings_frame, text="💾 Salvar Configurações", width=300, height=40, font=ctk.CTkFont(size=16, weight="bold"), fg_color="#1976D2", hover_color="#2196F3", text_color="white", command=self.save_settings_from_ui)
        save_button.pack(pady=30)
        return settings_frame

    def create_pacotes_frame(self):
        frame = ctk.CTkFrame(self)
        frame.grid_columnconfigure(0, weight=1)
        
        title_label = ctk.CTkLabel(frame, text="📦 Adicionar Pacote Completo", font=ctk.CTkFont(size=18, weight="bold"))
        title_label.grid(row=0, column=0, padx=10, pady=(20, 10), sticky="w")

        info_label = ctk.CTkLabel(
            frame,
            text=(
                "Cole a URL da lista de cursos (Meus cursos) ou de uma página de pacote. "
                "Ex.: .../app/dashboard/cursos — o app lê os cards e monta a fila com cada matéria."
            ),
            wraplength=700,
            justify="left",
        )
        info_label.grid(row=1, column=0, padx=10, pady=(0, 20), sticky="w")

        add_frame = ctk.CTkFrame(frame, fg_color="transparent")
        add_frame.grid(row=2, column=0, padx=10, pady=(10, 10), sticky="ew")
        add_frame.grid_columnconfigure(0, weight=1)
        
        self.new_package_url_entry = ctk.CTkEntry(
            add_frame,
            placeholder_text="📎 Ex: .../app/dashboard/cursos  ou  .../pacotes/...",
        )
        self.new_package_url_entry.grid(row=0, column=0, padx=(10, 5), pady=10, sticky="ew")
        
        self.add_package_button = ctk.CTkButton(add_frame, text="🔎 Buscar e Adicionar Matérias", width=220, fg_color="#3949AB", hover_color="#5C6BC0", text_color="white", command=self.add_package)
        self.add_package_button.grid(row=0, column=1, padx=(5, 10), pady=10)
        
        return frame

    def create_matriculas_catalog_frame(self):
        frame = ctk.CTkFrame(self)
        frame.grid_columnconfigure(0, weight=1)
        frame.grid_rowconfigure(3, weight=1)

        title = ctk.CTkLabel(
            frame, text="📋 Minhas matrículas (cursos disponíveis)", font=ctk.CTkFont(size=20, weight="bold")
        )
        title.grid(row=0, column=0, padx=14, pady=(16, 6), sticky="w")

        info = ctk.CTkLabel(
            frame,
            text=(
                "Conecta ao site já logado, percorre «Meus cursos» e «Assinaturas», extrai título e link de cada "
                f"curso e salva em {CATALOGO_MAPEADO_FILE.name}. Use os botões para enviar à fila de download."
            ),
            wraplength=720,
            justify="left",
        )
        info.grid(row=1, column=0, padx=14, pady=(0, 10), sticky="w")

        btn_row = ctk.CTkFrame(frame, fg_color="transparent")
        btn_row.grid(row=2, column=0, padx=10, pady=8, sticky="ew")

        self.matriculas_rescan_button = ctk.CTkButton(
            btn_row,
            text="🔄 Buscar cursos no site agora",
            width=240,
            fg_color="#1565C0",
            hover_color="#1976D2",
            command=self.start_matriculas_catalog_scan,
        )
        self.matriculas_rescan_button.pack(side="left", padx=6)

        self.matriculas_add_all_button = ctk.CTkButton(
            btn_row,
            text="➕ Colocar todos na fila",
            width=200,
            fg_color="#2E7D32",
            hover_color="#388E3C",
            command=self.add_all_mapped_courses_to_queue,
        )
        self.matriculas_add_all_button.pack(side="left", padx=6)

        self.matriculas_catalog_scroll = ctk.CTkScrollableFrame(
            frame, label_text="Cursos mapeados (nome + URL)"
        )
        self.matriculas_catalog_scroll.grid(row=3, column=0, padx=10, pady=10, sticky="nsew")
        self.matriculas_catalog_scroll.grid_columnconfigure(0, weight=1)
        return frame

    def create_materias_frame(self):
        frame = ctk.CTkFrame(self)
        frame.grid_columnconfigure(0, weight=1)
        frame.grid_rowconfigure(1, weight=1)
        
        add_frame = ctk.CTkFrame(frame, fg_color="transparent")
        add_frame.grid(row=0, column=0, padx=10, pady=(20, 10), sticky="ew")
        add_frame.grid_columnconfigure(0, weight=1) 

        self.new_url_entry = ctk.CTkEntry(
            add_frame,
            placeholder_text="📎 Ex: https://www.estrategiaconcursos.com.br/cursos/12345/.../aulas",
        )
        self.new_url_entry.grid(row=0, column=0, padx=(10, 5), pady=10, sticky="ew")
        
        add_button = ctk.CTkButton(add_frame, text="➕ Adicionar Matéria", width=180, fg_color="#3949AB", hover_color="#5C6BC0", text_color="white", command=self.add_url)
        add_button.grid(row=0, column=1, padx=(5, 5), pady=10)

        clear_button = ctk.CTkButton(add_frame, text="🗑️ Limpar Lista", width=140, fg_color="#C62828", hover_color="#E53935", text_color="white", command=self.clear_url_list)
        clear_button.grid(row=0, column=2, padx=(5, 10), pady=10)

        self.scrollable_urls_frame = ctk.CTkScrollableFrame(frame, label_text="📚 Fila de Download (Matérias Individuais)")
        self.scrollable_urls_frame.grid(row=1, column=0, padx=10, pady=10, sticky="nsew")
        self.scrollable_urls_frame.grid_columnconfigure(0, weight=1)
        return frame

    def _refresh_matriculas_catalog_from_disk(self) -> None:
        data: list = []
        try:
            with open(CATALOGO_MAPEADO_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError, TypeError):
            data = []
        if not isinstance(data, list):
            data = []
        self._cached_matriculas = data
        self._rebuild_matriculas_catalog_ui()

    def _rebuild_matriculas_catalog_ui(self) -> None:
        for w in self.matriculas_catalog_scroll.winfo_children():
            w.destroy()
        if not self._cached_matriculas:
            ctk.CTkLabel(
                self.matriculas_catalog_scroll,
                text="Nenhum curso listado. Use «Buscar cursos no site agora» (é preciso estar logado no navegador do app).",
                text_color="#888",
            ).pack(anchor="w", padx=8, pady=12)
            return
        for item in self._cached_matriculas:
            titulo = (item.get("titulo") or "Sem título").strip()
            url = (item.get("url") or "").strip()
            row = ctk.CTkFrame(self.matriculas_catalog_scroll, fg_color="#252525", corner_radius=6)
            row.pack(fill="x", padx=4, pady=4)
            row.grid_columnconfigure(0, weight=1)
            ctk.CTkLabel(
                row,
                text=f"{titulo}\n{url}",
                wraplength=640,
                justify="left",
                anchor="w",
                font=ctk.CTkFont(size=13),
            ).grid(row=0, column=0, padx=10, pady=8, sticky="ew")
            ctk.CTkButton(
                row,
                text="À fila",
                width=90,
                command=lambda u=url: self.append_single_course_to_queue(u),
            ).grid(row=0, column=1, padx=10, pady=8)

    def append_single_course_to_queue(self, url: str) -> None:
        u = ensure_course_aulas_url(normalize_estrategia_url(url))
        if not looks_like_individual_course_url(u):
            self.log_to_gui(MSG_CURSO_INVALIDO)
            return
        try:
            with open("course-urls.json", "r", encoding="utf-8") as f:
                cur = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            cur = []
        if u not in cur:
            cur.append(u)
            with open("course-urls.json", "w", encoding="utf-8") as f:
                json.dump(cur, f, indent=2, ensure_ascii=False)
            self.log_to_gui(f"Adicionado à fila: {u}")
        else:
            self.log_to_gui("URL já estava na fila.")

    def add_all_mapped_courses_to_queue(self) -> None:
        if not self._cached_matriculas:
            self.log_to_gui("Nada para adicionar. Faça o mapeamento primeiro.")
            return
        try:
            with open("course-urls.json", "r", encoding="utf-8") as f:
                cur = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            cur = []
        seen = set(cur)
        n = 0
        for item in self._cached_matriculas:
            u = ensure_course_aulas_url(normalize_estrategia_url(item.get("url") or ""))
            if not looks_like_individual_course_url(u):
                continue
            if u not in seen:
                seen.add(u)
                cur.append(u)
                n += 1
        if n:
            with open("course-urls.json", "w", encoding="utf-8") as f:
                json.dump(cur, f, indent=2, ensure_ascii=False)
        self.log_to_gui(f"{n} novo(s) na fila (total na fila: {len(cur)}).")

    def start_matriculas_catalog_scan(self) -> None:
        if self._matriculas_scan_running:
            return
        self._matriculas_scan_running = True
        self.matriculas_rescan_button.configure(state="disabled", text="Buscando...")
        self.show_logs_frame()

        def runner() -> None:
            exc: Optional[BaseException] = None
            courses: list = []
            try:
                courses = asyncio.run(scrape_matriculas_catalog_logic(self.log_queue))
            except BaseException as e:
                exc = e

            def done() -> None:
                self._matriculas_scan_running = False
                self.matriculas_rescan_button.configure(state="normal", text="🔄 Buscar cursos no site agora")
                if exc is not None:
                    self.log_to_gui(f"ERRO no mapeamento: {exc}")
                    return
                self._cached_matriculas = courses
                self._rebuild_matriculas_catalog_ui()
                self.select_frame(self.matriculas_catalog_frame)
                self.log_to_gui(f"Mapeamento concluído: {len(courses)} curso(s).")

            self.after(0, done)

        threading.Thread(target=runner, daemon=True).start()

    def browse_folder(self, entry_widget):
        folder_path = filedialog.askdirectory()
        if folder_path:
            entry_widget.delete(0, "end"); entry_widget.insert(0, folder_path)

    def load_settings_into_ui(self):
        for key, (widget, key_path) in self.settings_widgets.items():
            value_source = CONFIG
            if key_path:
                for p in key_path: value_source = value_source.get(p, {})
            lookup_key = "pastaDownloads" if key.startswith("pastaDownloads_") else key
            value = value_source.get(lookup_key)
            if isinstance(widget, ctk.CTkEntry):
                widget.delete(0, "end"); widget.insert(0, str(value if value is not None else ""))
            elif isinstance(widget, ctk.CTkComboBox):
                if key == "pdfType":
                    mapa_pdf = {
                        "1": "1: Simplificado",
                        "2": "2: Original",
                        "3": "3: Marcado",
                        "4": "4: Todos (+ extras)",
                        "5": "5: Simplificado + Marcado",
                    }
                    widget.set(mapa_pdf.get(str(value), "2: Original"))
                else: widget.set(str(value if value is not None else ""))
            elif isinstance(widget, ctk.CTkSwitch):
                widget.select() if value else widget.deselect()

    def save_settings_from_ui(self):
        for key, (widget, key_path) in self.settings_widgets.items():
            if isinstance(widget, ctk.CTkSwitch): value = widget.get() == 1
            elif isinstance(widget, ctk.CTkComboBox) and key == "pdfType":
                mapa_inverso = {
                    "1: Simplificado": 1,
                    "2: Original": 2,
                    "3: Marcado": 3,
                    "4: Todos (+ extras)": 4,
                    "4: Todos": 4,
                    "5: Simplificado + Marcado": 5,
                }
                value = mapa_inverso.get(widget.get(), 2)
            else: value = widget.get()
            if isinstance(value, str) and value.isdigit() and key != 'resolucaoEscolhida': value = int(value)
            target_dict = CONFIG
            if key_path:
                for p in key_path:
                    if p not in target_dict: target_dict[p] = {}
                    target_dict = target_dict[p]
            final_key = "pastaDownloads" if key.startswith("pastaDownloads_") else key
            target_dict[final_key] = value
        with open('config.json', 'w', encoding='utf-8') as f:
            json.dump(CONFIG, f, indent=4, ensure_ascii=False)
        self.settings_button.configure(text="⚙️ Configurações (Salvo!)")
        self.after(2000, lambda: self.settings_button.configure(text="⚙️ Configurações"))

    def load_urls_into_ui(self):
        for widget in self.scrollable_urls_frame.winfo_children(): widget.destroy()
        try:
            with open('course-urls.json', 'r', encoding='utf-8') as f: current_urls = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError): current_urls = []
        for i, url in enumerate(current_urls):
            url_frame = ctk.CTkFrame(self.scrollable_urls_frame, corner_radius=8, fg_color="#202020")
            url_frame.pack(fill="x", padx=5, pady=5)
            url_frame.grid_columnconfigure(0, weight=1)

            label = ctk.CTkLabel(url_frame, text=url, wraplength=600, justify="left", font=ctk.CTkFont(size=13))
            label.grid(row=0, column=0, padx=10, pady=8, sticky="w")
            
            buttons_frame = ctk.CTkFrame(url_frame, fg_color="transparent")
            buttons_frame.grid(row=0, column=1, padx=10, pady=8, sticky="e")

            up_button = ctk.CTkButton(buttons_frame, text="▲", width=30, command=lambda index=i: self.move_url(index, "up"))
            up_button.pack(side="left", padx=(0, 5))
            
            down_button = ctk.CTkButton(buttons_frame, text="▼", width=30, command=lambda index=i: self.move_url(index, "down"))
            down_button.pack(side="left", padx=(0, 5))

            remove_button = ctk.CTkButton(buttons_frame, text="❌", width=30, fg_color="#C62828", hover_color="#E53935", text_color="white", command=lambda u=url: self.remove_url(u))
            remove_button.pack(side="left", padx=(5, 0))

    def move_url(self, index, direction):
        try:
            with open('course-urls.json', 'r', encoding='utf-8') as f:
                current_urls = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return

        if direction == "up" and index > 0:
            current_urls.insert(index - 1, current_urls.pop(index))
        elif direction == "down" and index < len(current_urls) - 1:
            current_urls.insert(index + 1, current_urls.pop(index))
        
        with open('course-urls.json', 'w', encoding='utf-8') as f:
            json.dump(current_urls, f, indent=2, ensure_ascii=False)
        
        self.load_urls_into_ui()

    def add_url(self):
        new_url = normalize_estrategia_url(self.new_url_entry.get())
        if new_url and looks_like_individual_course_url(new_url):
            try:
                with open('course-urls.json', 'r', encoding='utf-8') as f: current_urls = json.load(f)
            except (FileNotFoundError, json.JSONDecodeError): current_urls = []
            if new_url not in current_urls:
                current_urls.append(new_url); self.new_url_entry.delete(0, "end")
                with open('course-urls.json', 'w', encoding='utf-8') as f: json.dump(current_urls, f, indent=2, ensure_ascii=False)
                self.load_urls_into_ui()
        else:
            self.log_to_gui(MSG_CURSO_INVALIDO)
    
    def remove_url(self, url_to_remove):
        with open('course-urls.json', 'r', encoding='utf-8') as f: current_urls = json.load(f)
        current_urls.remove(url_to_remove)
        with open('course-urls.json', 'w', encoding='utf-8') as f: json.dump(current_urls, f, indent=2, ensure_ascii=False)
        self.load_urls_into_ui()
        
    def clear_url_list(self):
        self.log_to_gui("Limpando a lista de matérias da fila de download...")
        try:
            with open('course-urls.json', 'w', encoding='utf-8') as f:
                json.dump([], f)
            self.log_to_gui("A lista de matérias foi limpa com sucesso.")
        except Exception as e:
            self.log_to_gui(f"ERRO: Não foi possível limpar o arquivo course-urls.json: {e}")
        
        self.load_urls_into_ui()
        
    def add_package(self):
        package_url = normalize_estrategia_url(self.new_package_url_entry.get())
        if not package_url or not looks_like_package_page_url(package_url):
            self.log_to_gui(MSG_PACOTE_INVALIDO)
            return

        self.add_package_button.configure(state="disabled", text="Buscando...")
        self.show_logs_frame()
        
        callback = lambda: self.add_package_button.configure(state="normal", text="🔎 Buscar e Adicionar Matérias")
        
        scrape_thread = threading.Thread(
            target=lambda: asyncio.run(scrape_package_logic(package_url, self.log_queue, callback)),
            daemon=True
        )
        scrape_thread.start()

    def start_download(self):
        if self.download_thread and self.download_thread.is_alive():
            self.log_to_gui("Um processo de download já está em andamento.")
            return
        self.progress_bar.set(0)
        self.start_button.configure(state="disabled", text="Baixando...")
        self.log_textbox.configure(state="normal"); self.log_textbox.delete("1.0", "end"); self.log_textbox.configure(state="disabled")
        self.show_logs_frame()
        self.download_thread = threading.Thread(target=lambda: asyncio.run(download_logic_main(self.update_progress, self.log_queue)), daemon=True)
        self.download_thread.start()

    def process_log_queue(self):
        try:
            while True:
                message = self.log_queue.get_nowait()
                self.log_to_gui(message)
        except queue.Empty: pass
        finally: self.after(100, self.process_log_queue)
    
    def log_to_gui(self, message):
        self.log_textbox.configure(state="normal")
        self.log_textbox.insert("end", message + "\n")
        self.log_textbox.see("end")
        self.log_textbox.configure(state="disabled")
        if (
            "PROCESSO FINALIZADO" in message
            or "Busca de matérias do pacote finalizada" in message
            or "--- FINALIZADO ---" in message
            or "Mapeamento concluído:" in message
        ):
            self.start_button.configure(state="normal", text="⏬ INICIAR DOWNLOADS")

    def update_progress(self, value):
        self.progress_bar.set(value)

if __name__ == "__main__":
    app = App()
    app.mainloop()
