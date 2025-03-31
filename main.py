#!/usr/bin/env python3

import argparse
import json
import logging
import os
import re
import time
import traceback
from datetime import datetime
from urllib.parse import urlparse, urljoin, urlunparse

import requests
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.common.exceptions import WebDriverException
from selenium.webdriver.chrome.options import Options

# Configuração de logging
logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

# Lista de User Agents para rotação
USER_AGENTS = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
    'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/92.0.4515.107 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 11_4) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/14.1.1 Safari/605.1.15',
]


class JSDomainCrawler:
    def __init__(self, domain_name, js_render_time=3, delay=1, max_pages=50,
                 max_depth=2, respect_robots=False, concurrency=1, output_dir='crawled_data'):
        """
        Inicializa o crawler para um domínio específico.

        Args:
            domain_name: O domínio principal para rastrear (ex: 'example.com')
            js_render_time: Tempo (segundos) para aguardar renderização de JavaScript
            delay: Tempo mínimo (segundos) entre requisições para o mesmo host
            max_pages: Número máximo de páginas para rastrear
            max_depth: Profundidade máxima do rastreamento
            respect_robots: Se deve respeitar as regras de robots.txt
            concurrency: Número de navegadores Selenium para usar simultaneamente
            output_dir: Diretório para salvar os resultados
        """
        # Configurações básicas
        self.domain_name = domain_name
        self.base_url = f"https://{domain_name}"
        self.delay = delay
        self.respect_robots = respect_robots
        self.max_pages = max_pages
        self.concurrency = concurrency
        self.js_render_time = js_render_time
        self.output_dir = output_dir

        # Verificar se o diretório de saída existe
        os.makedirs(output_dir, exist_ok=True)

        # Configurações para exclusão de URLs
        self.exclude_patterns = [
            r'/cdn-cgi/',
            r'/wp-admin/',
            r'/wp-includes/',
            r'/wp-content/uploads/',
            r'\.(jpg|jpeg|gif|png|svg|css|js|ico|xml|pdf|zip|gz|rar)$'
        ]

        # Inicializar variáveis relacionadas ao domínio original e domínios relacionados
        self.include_com_domain = False
        self.com_path_filter = None

        self.allowed_domains = [domain_name]

        # Estruturas de dados para o rastreamento
        self.url_queue = []  # (url, depth)
        self.visited_urls = set()
        self.page_contents = []
        self.host_last_access = {}  # {host: timestamp}
        self.robots_parsers = {}  # {host: parser}

        # Inicializar com a URL base
        self.url_queue.append((self.base_url, 0))

        # Adicionar domínio .com relacionado se relevante
        if self.include_com_domain:
            com_url = f"https://{self.com_domain}{self.com_path_filter}"
            self.url_queue.append((com_url, 0))

        # Configurações de profundidade
        self.max_depth = max_depth

        # Inicializar os drivers do Selenium
        self.drivers = self._initialize_selenium_drivers()

    def _initialize_selenium_drivers(self):
        """
        Inicializa os drivers do Selenium para renderização de JavaScript.

        Returns:
            list: Lista de instâncias do WebDriver
        """
        drivers = []

        # Verificar se o ChromeDriver está disponível
        chromedriver_path = '/usr/local/bin/chromedriver'
        chromedriver_exists = os.path.exists(chromedriver_path)
        logging.info(f"Verificando se ChromeDriver existe em {chromedriver_path}: {chromedriver_exists}")

        if not chromedriver_exists:
            logging.warning("ChromeDriver não encontrado. Selenium não será utilizado.")
            return []

        # Criar instâncias do Selenium
        for i in range(self.concurrency):
            try:
                logging.info(f"Iniciando navegador Selenium #{i + 1}")
                options = Options()
                options.add_argument('--headless=new')
                options.add_argument('--no-sandbox')
                options.add_argument('--disable-dev-shm-usage')
                options.add_argument('--disable-gpu')
                options.add_argument('--window-size=1920,1080')
                options.add_argument('--disable-extensions')

                # Usar um User-Agent aleatório
                options.add_argument(f'user-agent={USER_AGENTS[i % len(USER_AGENTS)]}')

                service = Service(chromedriver_path)
                driver = webdriver.Chrome(service=service, options=options)

                # Verificar se o navegador está realmente funcionando
                driver.get("about:blank")
                time.sleep(1)  # Esperar um momento para garantir que a página carregou

                drivers.append(driver)
                logging.info(f"Navegador Selenium #{i + 1} inicializado com sucesso")
            except Exception as e:
                logging.error(f"Erro ao inicializar navegador Selenium #{i + 1}: {str(e)}")
                logging.error(traceback.format_exc())

        return drivers

    def _get_robots_parser(self, url):
        """
        Obtém o parser robots.txt para um host específico.

        Args:
            url: A URL do site

        Returns:
            robotparser.RobotFileParser ou None
        """
        if not self.respect_robots:
            return None

        parsed_url = urlparse(url)
        host = parsed_url.netloc

        # Verificar se já temos um parser para este host
        if host in self.robots_parsers:
            return self.robots_parsers[host]

        # Criar um novo parser
        try:
            from urllib import robotparser
            parser = robotparser.RobotFileParser()
            parser.set_url(f"https://{host}/robots.txt")
            parser.read()
            self.robots_parsers[host] = parser
            return parser
        except Exception as e:
            logging.warning(f"Erro ao processar robots.txt para {host}: {e}")
            return None

    def _normalize_url(self, url, base_url=None):
        """
        Normaliza URLs para evitar duplicações.

        Args:
            url (str): URL para normalizar
            base_url (str, optional): URL base para resolução de URLs relativas

        Returns:
            str: URL normalizada
        """
        # Resolve URL relativa se base_url for fornecida
        if base_url:
            from urllib.parse import urljoin
            url = urljoin(base_url, url)

        # Parseia a URL
        parsed_url = urlparse(url)

        # Remove fragmentos
        url_without_fragment = parsed_url._replace(fragment='')

        # Normaliza o caminho
        normalized_path = self._normalize_path(url_without_fragment.path)
        url_without_fragment = url_without_fragment._replace(path=normalized_path)

        # Converte para string
        normalized_url = urlunparse(url_without_fragment)

        # Remove a barra final, se existir, exceto para URLs raiz
        if normalized_url.endswith('/') and normalized_url != f"{parsed_url.scheme}://{parsed_url.netloc}/":
            normalized_url = normalized_url.rstrip('/')

        # Remove o www. se presente
        normalized_url = normalized_url.replace('://www.', '://')

        # Padroniza o protocolo (https)
        if normalized_url.startswith('http://'):
            normalized_url = normalized_url.replace('http://', 'https://')

        return normalized_url

    def _normalize_path(self, path):
        """
        Normaliza o caminho da URL.

        Args:
            path (str): Caminho da URL

        Returns:
            str: Caminho normalizado
        """
        # Remove barras duplas
        path = path.replace('//', '/')

        # Remove barras no início e no final
        path = path.strip('/')

        return path

    def _is_allowed_url(self, url):
        """
        Verifica se uma URL é permitida para rastreamento.

        Args:
            url: A URL para verificar

        Returns:
            bool: True se a URL for permitida
        """
        try:
            parsed_url = urlparse(url)
            host = parsed_url.netloc

            # Verificar se o domínio é permitido
            if host not in self.allowed_domains:
                return False

            # Tratamento especial para o domínio .com
            if self.include_com_domain and host == self.com_domain:
                # Para o domínio .com, verificar se contém o caminho específico
                if not parsed_url.path.startswith(self.com_path_filter):
                    return False

            # Verificar robots.txt
            if self.respect_robots:
                parser = self._get_robots_parser(url)
                if parser and not parser.can_fetch("*", url):
                    logging.info(f"URL {url} bloqueada por robots.txt")
                    return False

            return True
        except Exception as e:
            logging.warning(f"Erro ao verificar permissão para URL {url}: {e}")
            return False

    def _should_exclude(self, url):
        """
        Verifica se uma URL deve ser excluída com base nos padrões de exclusão.

        Args:
            url: A URL para verificar

        Returns:
            bool: True se a URL deve ser excluída
        """
        for pattern in self.exclude_patterns:
            if re.search(pattern, url, re.IGNORECASE):
                return True
        return False

    def _extract_links(self, html, base_url):
        """
        Extrai links de uma página HTML.

        Args:
            html: Conteúdo HTML da página
            base_url: URL base para resolver URLs relativas

        Returns:
            list: Lista de URLs extraídas e normalizadas
        """
        soup = BeautifulSoup(html, 'html.parser')
        links = []

        for anchor in soup.find_all('a', href=True):
            href = anchor['href']
            normalized_url = self._normalize_url(href, base_url)

            if (normalized_url and
                    normalized_url not in self.visited_urls and
                    self._is_allowed_url(normalized_url) and
                    not self._should_exclude(normalized_url)):
                links.append(normalized_url)

        return links

    def _extract_body_content(self, html, url, ignored_tags=None):
        """
        Extrai conteúdo relevante de uma página HTML em formato Markdown.

        Args:
            html: Conteúdo HTML da página
            url: URL da página
            ignored_tags: Lista de tags para serem removidas (opcional)

        Returns:
            dict: Conteúdo extraído em Markdown e metadados
        """
        # Obtém as tags a serem ignoradas dos kwargs (com fallback para tags padrão)
        ignored_tags = getattr(self, '_extract_body_content_kwargs', {}).get(
            'ignored_tags',
            ['script', 'style', 'meta', 'link', 'img', 'svg', 'header', 'footer', 'nav']
        )

        soup = BeautifulSoup(html, 'html.parser')

        # Remover tags especificadas
        for element in soup(ignored_tags):
            element.decompose()

        # Extrair título
        title = soup.title.string if soup.title else ""

        # Converter HTML para Markdown
        markdown_content = self._convert_html_to_markdown(soup)

        return {
            'url': url,
            'title': title,
            'content': markdown_content,
            'timestamp': datetime.now().isoformat()
        }

    def _convert_html_to_markdown(self, soup):
        """
        Converte conteúdo HTML para Markdown.

        Args:
            soup: Objeto BeautifulSoup

        Returns:
            str: Conteúdo em formato Markdown
        """
        # Processamento de cabeçalhos
        for h in soup.find_all(['h1', 'h2', 'h3', 'h4', 'h5', 'h6']):
            level = int(h.name[1])
            h.string = f"{'#' * level} {h.get_text()}"

        # Processamento de links
        for a in soup.find_all('a'):
            if a.has_attr('href'):
                a.string = f"[{a.get_text()}]({a['href']})"

        # Processamento de ênfase
        for strong in soup.find_all(['strong', 'b']):
            strong.string = f"**{strong.get_text()}**"

        for em in soup.find_all(['em', 'i']):
            em.string = f"*{em.get_text()}*"

        # Processamento de listas
        for ul in soup.find_all('ul'):
            for li in ul.find_all('li'):
                li.string = f"- {li.get_text()}"

        for ol in soup.find_all('ol'):
            for i, li in enumerate(ol.find_all('li'), 1):
                li.string = f"{i}. {li.get_text()}"

        # Processamento de código
        for code in soup.find_all('code'):
            code.string = f"`{code.get_text()}`"

        # Processamento de parágrafos
        for p in soup.find_all('p'):
            p.string = p.get_text()

        # Limpar espaços em branco extras
        markdown_text = soup.get_text(separator='\n').strip()
        markdown_text = re.sub(r'\n+', '\n', markdown_text)
        markdown_text = re.sub(r' +', ' ', markdown_text)

        return markdown_text

    def _respect_crawl_delay(self, url):
        """
        Respeita o intervalo entre requisições para o mesmo host.

        Args:
            url: URL para a qual a requisição será feita
        """
        parsed_url = urlparse(url)
        host = parsed_url.netloc

        # Verificar o delay de robots.txt se necessário
        robot_delay = None
        if self.respect_robots:
            parser = self._get_robots_parser(url)
            if parser:
                robot_delay = parser.crawl_delay("*")

        # Usar o maior valor entre o delay configurado e o de robots.txt
        delay = max(self.delay, robot_delay or 0)

        # Verificar quando foi a última requisição para este host
        last_access = self.host_last_access.get(host)
        if last_access:
            elapsed = time.time() - last_access
            if elapsed < delay:
                sleep_time = delay - elapsed
                time.sleep(sleep_time)

        # Atualizar o timestamp de acesso
        self.host_last_access[host] = time.time()

    def _get_available_driver(self):
        """
        Obtém um driver Selenium disponível, mantendo consistência.

        Returns:
            tuple: (índice do driver, instância do driver) ou (None, None)
        """
        # Verificar se temos drivers disponíveis
        if not self.drivers:
            logging.warning("Sem drivers Selenium disponíveis")
            return None, None

        # Redefinir a variável de classe se necessário para evitar referência vazia
        if len(self.drivers) == 0:
            logging.warning("Array de drivers vazio, reinicializando")
            self.drivers = self._initialize_selenium_drivers()
            if len(self.drivers) == 0:
                return None, None

        # Verificar cada driver para garantir que está em um estado utilizável
        valid_drivers = []
        for i, driver in enumerate(self.drivers):
            try:
                # Tentar acessar uma propriedade para verificar se o driver está funcional
                _ = driver.current_url
                valid_drivers.append((i, driver))
            except Exception as e:
                logging.warning(f"Driver #{i} não está mais válido: {e}")
                # Tentar reiniciar o driver
                try:
                    driver.quit()
                except:
                    pass

                try:
                    options = Options()
                    options.add_argument('--headless=new')
                    options.add_argument('--no-sandbox')
                    options.add_argument('--disable-dev-shm-usage')
                    options.add_argument('--disable-gpu')
                    options.add_argument('--window-size=1920,1080')
                    options.add_argument('--disable-extensions')

                    service = Service('/usr/local/bin/chromedriver')
                    self.drivers[i] = webdriver.Chrome(service=service, options=options)
                    valid_drivers.append((i, self.drivers[i]))
                    logging.info(f"Driver #{i} reinicializado com sucesso")
                except Exception as e:
                    logging.error(f"Falha ao reiniciar driver #{i}: {e}")

        if not valid_drivers:
            logging.error("Nenhum driver válido disponível")
            return None, None

        # Usar o primeiro driver válido disponível
        return valid_drivers[0]

    def _fetch_with_selenium(self, url):
        """
        Busca uma página usando Selenium para renderizar JavaScript.

        Args:
            url: URL para buscar

        Returns:
            str: Conteúdo HTML da página renderizada ou None em caso de erro
        """
        driver_index, driver = self._get_available_driver()
        if not driver:
            logging.warning(f"Sem navegador disponível para renderizar {url}")
            return None

        try:
            logging.info(f"Acessando {url} com Selenium (driver #{driver_index})")
            driver.get(url)

            # Aguardar o carregamento do JavaScript
            time.sleep(self.js_render_time)

            # Rolar a página para carregar conteúdo lazy-loaded
            try:
                driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
                time.sleep(1)  # Esperar um pouco após a rolagem
            except:
                pass

            html_content = driver.page_source
            return html_content

        except WebDriverException as e:
            logging.error(f"Erro do WebDriver ao acessar {url}: {str(e)}")
            return None

        except Exception as e:
            logging.error(f"Erro ao acessar {url} com Selenium: {str(e)}")
            return None

    def crawl(self):
        """
        Inicia o processo de rastreamento do domínio.

        Returns:
            list: Conteúdo extraído de todas as páginas rastreadas
        """
        logging.info(f"Iniciando crawler no(s) domínio(s): {', '.join(self.allowed_domains)}")

        pages_processed = 0

        # Garantir que temos drivers disponíveis
        if not hasattr(self, 'drivers') or not self.drivers:
            logging.warning("Inicializando drivers do Selenium novamente")
            self.drivers = self._initialize_selenium_drivers()

        try:
            while self.url_queue and pages_processed < self.max_pages:
                url, depth = self.url_queue.pop(0)

                # Verificar se já visitamos esta URL
                if url in self.visited_urls:
                    continue

                # Marcar como visitada
                self.visited_urls.add(url)

                # Respeitar delays entre requisições
                self._respect_crawl_delay(url)

                logging.info(
                    f"Processando {url} (profundidade: {depth}, páginas: {pages_processed + 1}/{self.max_pages})")

                # Obter conteúdo da página com Selenium
                html_content = self._fetch_with_selenium(url)

                if not html_content:
                    logging.warning(f"Não foi possível obter conteúdo de {url}")
                    continue

                # Extrair conteúdo
                page_content = self._extract_body_content(html_content, url)
                self.page_contents.append(page_content)

                pages_processed += 1

                # Salvar resultados intermediários a cada 10 páginas
                if pages_processed % 10 == 0:
                    self._save_intermediate_results()

                # Se ainda não atingimos a profundidade máxima, extrair links
                if depth < self.max_depth:
                    links = self._extract_links(html_content, url)

                    # Adicionar links à fila
                    for link in links:
                        if link not in self.visited_urls:
                            self.url_queue.append((link, depth + 1))

        except KeyboardInterrupt:
            logging.info("Crawler interrompido pelo usuário")

        except Exception as e:
            logging.error(f"Erro durante o crawling: {str(e)}")
            logging.error(traceback.format_exc())

        finally:
            # Salvar resultados
            self.save_to_json()

            # Limpar recursos
            self._cleanup_drivers()

        return self.page_contents

    def _cleanup_drivers(self):
        """
        Fecha todos os drivers Selenium.
        """
        if hasattr(self, 'drivers') and self.drivers:
            logging.info(f"Fechando {len(self.drivers)} navegadores Selenium")
            for i, driver in enumerate(self.drivers):
                try:
                    driver.quit()
                    logging.info(f"Navegador #{i + 1} fechado com sucesso")
                except Exception as e:
                    logging.warning(f"Erro ao fechar navegador #{i + 1}: {str(e)}")

            self.drivers = []

    def _save_intermediate_results(self):
        """
        Salva os resultados parciais em um arquivo JSON.
        """
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        output_file = os.path.join(self.output_dir, f"crawl_intermediate_{timestamp}.json")

        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(self.page_contents, f, ensure_ascii=False, indent=2)

        logging.info(f"Resultados intermediários salvos em {output_file}")

    def save_to_json(self, filename=None):
        """
        Salva os resultados do crawling em um arquivo JSON.

        Args:
            filename: Nome do arquivo para salvar (opcional)
        """
        if not filename:
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            filename = f"crawl_{self.domain_name}_{timestamp}.json"

        output_file = os.path.join(self.output_dir, filename)

        # Criar relatório final
        report = {
            'domain': self.domain_name,
            'crawl_date': datetime.now().isoformat(),
            'total_pages': len(self.page_contents),
            'pages': self.page_contents
        }

        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(report, f, ensure_ascii=False, indent=2)

        logging.info(
            f"Crawler concluído. {len(self.page_contents)} páginas processadas. Resultados salvos em {output_file}")

        return output_file


def main():
    parser = argparse.ArgumentParser(description='Crawler para extração de conteúdo web')
    parser.add_argument('url', help='URL para iniciar o crawling')
    parser.add_argument('--max-pages', type=int, default=10,
                        help='Número máximo de páginas para crawlear')
    parser.add_argument('--max-depth', type=int, default=3,
                        help='Profundidade máxima de navegação')
    parser.add_argument('--ignored-tags', type=str, default='script,style,meta,link',
                        help='Tags HTML para ignorar, separadas por vírgula')

    args = parser.parse_args()

    # Converte a string de tags em uma lista
    ignored_tags = [tag.strip() for tag in args.ignored_tags.split(',') if tag.strip()]

    crawler = JSDomainCrawler(
        domain_name=args.url,
        max_pages=args.max_pages,
        max_depth=args.max_depth
    )

    # Passa as tags a serem ignoradas para o método de extração de conteúdo
    crawler._extract_body_content_kwargs = {
        'ignored_tags': ignored_tags
    }

    crawler.crawl()


if __name__ == "__main__":
    main()
