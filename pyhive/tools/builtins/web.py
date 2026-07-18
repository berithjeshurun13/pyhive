import asyncio
import re
import urllib.parse
from typing import Any, Dict, List, Tuple

import httpx
import wikipediaapi
from bs4 import BeautifulSoup
from typing_extensions import Literal

# Global configurations
USER_AGENT_STR = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/118.0.0.0 Safari/537.36"
)

headers = {"User-Agent": USER_AGENT_STR}

wiki = wikipediaapi.Wikipedia(
    user_agent=USER_AGENT_STR,
    language="en",
    extract_format=wikipediaapi.ExtractFormat.WIKI,
)


def clean_html(html: str) -> Tuple[BeautifulSoup, str]:
    soup = BeautifulSoup(html, "html.parser")
    clean_text = " ".join(soup.stripped_strings)
    return soup, clean_text


def extract_clean_text(html: str) -> str:
    """
    Extracts only meaningful text content from an HTML page for LLM input.
    Removes scripts, styles, navbars, footers, forms, etc.
    """
    soup, text = clean_html(html)

    for tag in soup(
        [
            "script",
            "style",
            "noscript",
            "header",
            "footer",
            "nav",
            "aside",
            "form",
            "svg",
            "link",
            "meta",
            "iframe",
            "picture",
            "source",
            "button",
            "input",
            "textarea",
            "select",
            "option",
        ]
    ):
        tag.decompose()

    for noisy in soup.select(
        '[class*="nav"], [class*="menu"], [class*="footer"], [class*="header"], '
        '[id*="nav"], [id*="menu"], [id*="footer"], [id*="header"], '
        '[class*="ad"], [id*="ad"], [class*="banner"], [id*="banner"]'
    ):
        noisy.decompose()

    # text = soup.get_text(separator="\n")
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    clean_text = "\n".join(lines)

    return re.sub(r"\s+", " ", clean_text).strip()


def return_ddg_redirect(link: str) -> str:
    if "uddg=" not in link:
        return link
    try:
        uddg = link.split("uddg=")[1].split("&")[0]
        return urllib.parse.unquote(uddg)
    except Exception:
        return link


async def wiki_event_api(
    mode: Literal["events", "deaths"], date: int, month: int, timeout: int = 5
) -> Dict[str, Any]:
    """Asynchronous version of your original historical events endpoint."""
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.get(
                f"https://byabbe.se/on-this-day/{month}/{date}/{mode}.json"
            )
            if r.status_code == 200:
                return {"status": True, "data": r.json()}
            return {"status": False, "R": f"HTTP Error {r.status_code}"}
    except Exception as e:
        return {"status": False, "R": str(e)}


async def _fetch_source_text(
    client: httpx.AsyncClient, link: str, timeout: int
) -> tuple[str, str | None]:
    """Helper coroutine to download and clean single external target pages concurrently."""
    try:
        response = await client.get(
            link, headers=headers, timeout=timeout, follow_redirects=True
        )
        if response.status_code == 200:
            return link, extract_clean_text(response.text)
    except Exception:
        pass
    return link, None


async def web_search(
    query: str, num: int = 30, timeout: int = 5
) -> Dict[str, Any] | bool:
    sources: List[str] = []
    summary_meta: Dict[str, Any] = {}
    w_search_data: Dict[str, Any] = {}
    solved_pbj: Dict[str, str] = {}

    try:
        async with httpx.AsyncClient(
            headers=headers, timeout=timeout, follow_redirects=True
        ) as client:
            # --- 1. DuckDuckGo Scrape ---
            try:
                ddg_html_url = (
                    f"https://html.duckduckgo.com/html/?q={urllib.parse.quote(query)}"
                )
                parser1 = await client.get(ddg_html_url)
                if parser1.status_code == 200:
                    soup = BeautifulSoup(parser1.text, "html.parser")
                    for a in soup.select("a.result__a"):
                        href = a.get("href", "")
                        if "uddg=" in str(href):
                            ul = return_ddg_redirect(link=str(href))
                            sources.append(ul)
                            if len(sources) >= num:
                                break
            except Exception:
                pass

            # --- 2. DuckDuckGo Instant Answer API ---
            try:
                ddg_api_url = f"https://api.duckduckgo.com/?q={urllib.parse.quote(query)}&format=json&skip_disambig=1&no_html=1"
                parser2 = await client.get(ddg_api_url)
                if parser2.status_code == 200:
                    summary_meta = parser2.json()
            except Exception:
                pass

            # --- 3. Wikipedia Search & Content Selection ---
            researche: List[str] = []
            try:
                wiki_api_url = "https://en.wikipedia.org/w/api.php"
                wiki_params = {
                    "action": "opensearch",
                    "namespace": "0",
                    "search": query,
                    "limit": str(max(5, int(num / 2))),
                    "format": "json",
                }
                wiki_resp = await client.get(wiki_api_url, params=wiki_params)
                if wiki_resp.status_code == 200:
                    researche = wiki_resp.json()[1]
            except Exception:
                pass

            target_page_title = researche[0] if researche else query

            try:
                loop = asyncio.get_running_loop()
                page = await loop.run_in_executor(None, wiki.page, target_page_title)

                if page.exists():
                    w_search_data[page.title] = {
                        "summary": page.summary,
                        "content": page.text,
                    }
            except Exception:
                pass

            # --- 4. Concurrent URL Processing ---
            if sources:
                tasks = [_fetch_source_text(client, link, timeout) for link in sources]
                completed_tasks = await asyncio.gather(*tasks)

                for link, cleaned_text in completed_tasks:
                    if cleaned_text:
                        solved_pbj[link] = cleaned_text

        return {"meta": summary_meta, "wiki": w_search_data, "api": solved_pbj}

    except Exception:
        return False


# if __name__ == "__main__":
#     result = asyncio.run(web_search("Venera 13"))
#     import json

#     with open("web_search_result.json", "w") as f:
#         json.dump(result, f, indent=4)
