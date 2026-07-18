
from ..wrappers import cache
from ..utils import ContextUpdater
from bs4 import BeautifulSoup
from typing_extensions import Literal
import requests, json, wikipedia, re
import urllib.parse

def extract_clean_text(html: str) -> str:
    """
    Extracts only meaningful text content from an HTML page for LLM input.
    Removes scripts, styles, navbars, footers, forms, etc.
    """

    soup = BeautifulSoup(html, "html.parser")

    for tag in soup([
        "script", "style", "noscript", "header", "footer", "nav", "aside",
        "form", "svg", "link", "meta", "iframe", "picture", "source", "button",
        "input", "textarea", "select", "option"
    ]):
        tag.decompose()

    for noisy in soup.select('[class*="nav"], [class*="menu"], [class*="footer"], [class*="header"], '
                             '[id*="nav"], [id*="menu"], [id*="footer"], [id*="header"], '
                             '[class*="ad"], [id*="ad"], [class*="banner"], [id*="banner"]'):
        noisy.decompose()

    text = soup.get_text(separator="\n")

    lines = [line.strip() for line in text.splitlines() if line.strip()]
    clean_text = "\n".join(lines)

    clean_text = re.sub(r'\s+', ' ', clean_text).strip()

    return clean_text


def return_ddg_redirect(link: str) -> str:
    if "uddg=" not in link:
        return link
    try:
        uddg = link.split("uddg=")[1].split("&")[0]
        return urllib.parse.unquote(uddg)
    except Exception:
        return link



def wiki_event_api(mode : Literal['events', 'deaths'], date : int, month : int) :
    try :
        r = requests.get(url=f'https://byabbe.se/on-this-day/{month}/{date}/{mode}.json')
        return {'status' : True, 'data' : r.json()}
    except Exception as e :
        return {'status' : False, 'R' : str(e)}


def clean_html(html):
    soup = BeautifulSoup(html, "html.parser")
    return " ".join(soup.stripped_strings)



class UnifiedSearchEngine(object) :
    def __init__(self, web_logger_address = None):
        super().__init__()
        self.__web_log_register(web_logger_address)

    def __web_log_register(self, addr) :

        self.___addr = addr

    def __log(self, data : dict) :
        if self.___addr == None :
            return
        try :
            requests.post(url=self.___addr, json=data)
        except : pass 
        return

    @cache
    def fetch(self, module : Literal['search-web', 'search-summary', 'wiki', 'all'] = 'all', **kwargs) -> dict :
        """
        Query's or gathers information from different sources from the web.

        Parameters:
        - query: str or list of str - string or list of strings of queries
        - solve: bool - to automaitcally fetch data from urls instead of returning plain urls

        Returns:
        - dict with keys:
        """
        headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                            "AppleWebKit/537.36 (KHTML, like Gecko) "
                            "Chrome/118.0.0.0 Safari/537.36"
        }
        
        def search_web() :
            url = 'https://html.duckduckgo.com/html/'
            query : str = str(kwargs.get('query', 'Nikola Tesla')).replace(' ', '+')
            url = url + f'?q={query}'
            search_level : int = kwargs.get('search_level', 0)
            url = url + f'&kp={search_level}'
            results : int = kwargs.get('results', 30)
            url = url + f'&dc={results}'
            print (url)
            urls : list[str] = []
            returnable = {'status' : False, 'R' : 'No Processed'}
            with ContextUpdater(self.___addr) as cf :
                cf.update({'mode' : 'SYLPH.SEARCH.INIT', 'message' : 'Gathering Resources...'})
                try :
                    parser = requests.get(url=url, headers=headers)
                    html = parser.text
                    soup = BeautifulSoup(html, "html.parser")
                    for a in soup.select("a.result__a"):
                        href = a.get("href", "")
                        if "uddg=" in href:
                            ul = return_ddg_redirect(link=href)
                            urls.append(ul)
                            cf.update({'mode' : 'SYLPH.SEARCH.PROC', 'message' : f'{urllib.parse.urlparse(ul).hostname}'})
                    if kwargs.get('solve', False) == True :
                        returnable = {
                            'status' : True,
                            'data'   : html,
                            'solved' : self.from_sources(urls, called=True),
                            'sources' : urls,
                            'cSources' : len(urls)
                        }
                    else :
                        returnable =  {'status' : True, 'data' : html, 'sources' : urls}
                except Exception as e :
                    returnable =  {'status' : False, 'R' : str(e)}
            return returnable 
        
        def search_summary() :
            url = f'https://api.duckduckgo.com/?q={str(kwargs.get("query", "Nikola Tesla")).replace(" ", "+")}&format=json&skip_disambig=1&no_html=1'
            try :
                parser = requests.get(url=url, headers=headers)
                return dict(json.loads(parser.text))
            except Exception as e :
                return {'status' : False, 'R' : str(e)}
            
        def search_wiki() :
            data = dict()
            with ContextUpdater(self.___addr) as cf :
                try :
                    def sve(title) :
                        content = wikipedia.page(title=title)
                        return {
                            'content' : content.content,
                            'categories' : content.categories,
                            'images' : content.images,
                            'links' : content.links,
                            'references' : content.references,
                            'sections' : content.sections,
                            'summary' : content.summary,
                            'source' : content.url
                        }
                    cf.update({'mode' : 'SYLPH.SEARCH.START', 'message' : f'Searching for {kwargs.get("query", "Nikola Tesla")}', 'cSources' : kwargs.get('results', 10)})
                    results, suggesstions = wikipedia.search(query=kwargs.get('query', 'Nikola Tesla'), suggestion=True, results=kwargs.get('results', 10))
                    if len(results) != 0 :
                        for title in results :
                            cf.update({'mode' : 'SYLPH.SEARCH.PROC', 'message' : f'{title} - wikipedia.org'})
                            data[title] = sve(title)
                    if len(suggesstions) != 0 :
                        for title in suggesstions :
                            title = str(title).capitalize()
                            data[title] = sve(title)
                    data['status'] = True
                except Exception as e :
                    data = {'status' : False, 'R' : str(e), '_broken' : data}
            return data
        
        if module == 'search-web' :
            return search_web()
        elif module == 'search-summary' :
            return search_summary()
        elif module == 'wiki' :
            return search_wiki()
        elif module == 'all' :
            return {
                'web' : search_web(),
                'web-api' : search_summary(),
                'wiki' : search_wiki(),
            }
    @cache
    def from_url(self, url: str, method: Literal['get', 'post'] = 'get', **kwargs) -> dict:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/118.0.0.0 Safari/537.36"
        }
        try:
            if method.lower() == 'post':
                r = requests.post(url, headers=headers, data=kwargs.get('data', {}))
            else:
                r = requests.get(url, headers=headers)
            return {'status': True, 'code': r.status_code, 'data': r.text}
        except Exception as e:
            return {'status': False, 'error': str(e)}



    def from_sources(self, sources : list[str], **kwargs) -> dict :
        data = dict()
        with ContextUpdater(self.___addr) as cf :
            for link in sources :
                headers = {
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                "AppleWebKit/537.36 (KHTML, like Gecko) "
                                "Chrome/118.0.0.0 Safari/537.36"
                }
                try :
                        cf.update({'mode' : 'SYLPH.SEARCH.PROC', 'message' : f'{urllib.parse.urlparse(link).hostname}'})
                        parser = requests.get(url=link, headers=headers)
                        data['status'] = True
                        data[link] = extract_clean_text(parser.text)
                except Exception as e :
                    data['status'] = False
                    data[link] = str(e)
        return data
    
