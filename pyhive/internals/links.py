from typing import ClassVar, Dict, Literal, List
from pydantic import BaseModel, Field, computed_field
import feedparser

class API(BaseModel):
    VERSION: ClassVar[float] = 1.0

class ApiNews(API):
    BASE_VERSION: ClassVar[float] = 0.1

    feeds: Dict[str, str] = Field(
        default={
            "us": "https://rss.nytimes.com/services/xml/rss/nyt/US.xml",
            "world": "https://rss.nytimes.com/services/xml/rss/nyt/World.xml",
            "africa": "https://rss.nytimes.com/services/xml/rss/nyt/Africa.xml",
            "america": "https://rss.nytimes.com/services/xml/rss/nyt/Americas.xml",
            "europe": "https://rss.nytimes.com/services/xml/rss/nyt/Europe.xml",
            "middleeast": "https://rss.nytimes.com/services/xml/rss/nyt/MiddleEast.xml",
            "education": "https://rss.nytimes.com/services/xml/rss/nyt/Education.xml",
            "politics": "https://rss.nytimes.com/services/xml/rss/nyt/Politics.xml",
            "business": "https://rss.nytimes.com/services/xml/rss/nyt/Business.xml",
            "energyenvironment": "https://rss.nytimes.com/services/xml/rss/nyt/EnergyEnvironment.xml",
            "smallbusiness": "https://rss.nytimes.com/services/xml/rss/nyt/SmallBusiness.xml",
            "economy": "https://rss.nytimes.com/services/xml/rss/nyt/Economy.xml",
            "dealbook": "https://rss.nytimes.com/services/xml/rss/nyt/Dealbook.xml",
            "media": "https://rss.nytimes.com/services/xml/rss/nyt/MediaandAdvertising.xml",
            "money": "https://rss.nytimes.com/services/xml/rss/nyt/YourMoney.xml",
            "tech": "https://rss.nytimes.com/services/xml/rss/nyt/Technology.xml",
            "personaltech": "https://rss.nytimes.com/services/xml/rss/nyt/PersonalTech.xml",
            "sports": "https://rss.nytimes.com/services/xml/rss/nyt/Sports.xml",
            "collegebasketball": "https://rss.nytimes.com/services/xml/rss/nyt/CollegeBasketball.xml",
            "collegefootball": "https://rss.nytimes.com/services/xml/rss/nyt/CollegeFootball.xml",
            "golf": "https://rss.nytimes.com/services/xml/rss/nyt/Golf.xml",
            "hockey": "https://rss.nytimes.com/services/xml/rss/nyt/Hockey.xml",
            "tennis": "https://rss.nytimes.com/services/xml/rss/nyt/Tennis.xml",
            "soccer": "https://rss.nytimes.com/services/xml/rss/nyt/Soccer.xml",
            "probasketball": "https://rss.nytimes.com/services/xml/rss/nyt/ProBasketball.xml",
            "profootball": "https://rss.nytimes.com/services/xml/rss/nyt/ProFootball.xml",
            "science": "https://rss.nytimes.com/services/xml/rss/nyt/Science.xml",
            "space": "https://rss.nytimes.com/services/xml/rss/nyt/Space.xml",
            "health": "https://rss.nytimes.com/services/xml/rss/nyt/Health.xml",
            "well": "https://rss.nytimes.com/services/xml/rss/nyt/Well.xml",
            "climate": "https://rss.nytimes.com/services/xml/rss/nyt/Climate.xml",
            "weather": "https://rss.nytimes.com/services/xml/rss/nyt/Weather.xml",
            "arts": "https://rss.nytimes.com/services/xml/rss/nyt/Arts.xml",
            "design": "https://rss.nytimes.com/services/xml/rss/nyt/ArtandDesign.xml",
            "dance": "https://rss.nytimes.com/services/xml/rss/nyt/Dance.xml",
            "movies": "https://rss.nytimes.com/services/xml/rss/nyt/Movies.xml",
            "music": "https://rss.nytimes.com/services/xml/rss/nyt/Music.xml",
            "television": "https://rss.nytimes.com/services/xml/rss/nyt/Television.xml",
            "theater": "https://rss.nytimes.com/services/xml/rss/nyt/Theater.xml",
            "fashion": "https://rss.nytimes.com/services/xml/rss/nyt/FashionandStyle.xml",
            "food": "https://rss.nytimes.com/services/xml/rss/nyt/DiningandWine.xml",
            "wedding": "https://rss.nytimes.com/services/xml/rss/nyt/Weddings.xml",
            "tmagazine": "https://rss.nytimes.com/services/xml/rss/nyt/tmagazine.xml",
            "travel": "https://rss.nytimes.com/services/xml/rss/nyt/Travel.xml",
        }
    )

    def get_feed(self, category: str) -> str:
        """Returns the URL for a specific category, or a fallback message if not found."""
        return self.feeds.get(category.lower(), "Category not found")

# https://www.theguardian.com/world/rss

def parse_bbc_world_news() -> None:
    url = "https://feeds.bbci.co.uk/news/world/rss.xml"
    print(f"Fetching and parsing: {url}...\n")
    feed = feedparser.parse(url)
    if feed.bozo:
        print("Warning: The parser encountered an issue or the feed is malformed.")
    feed_title = feed.feed.get("title", "No Title")
    feed_description = feed.feed.get("description", "No Description")
    print(f"=== Feed Title: {feed_title} ===")
    print(f"=== Description: {feed_description} ===\n")
    newsX : List[Dict] = []
    for index, entry in enumerate(feed.entries, start=1):
        title = entry.get("title", "No Title")
        link = entry.get("link", "No Link")
        summary = entry.get("summary", "No Description Available.")
        published = entry.get("published", "Unknown Date")
        newsX.append({"published" : published, "summary" : summary, "link" : link, "title" : title, "thumbnail" : entry.get('media_thumbnail')})
    return newsX


def parse_nyt_feed(url: str = "https://rss.nytimes.com/services/xml/rss/nyt/US.xml") -> List[Dict]:
    print(f"Fetching NYT feed: {url}\n")
    feed = feedparser.parse(url)
    channel_title = feed.feed.get("title", "New York Times")
    channel_link = feed.feed.get("link", "https://www.nytimes.com")
    print(f"=== {channel_title} ===")
    print(f"Main Site: {channel_link}\n" + "="*40 + "\n")
    newsX : List[Dict] = []
    for i, entry in enumerate(feed.entries, start=1):
        title = entry.get("title", "No Title")
        link = entry.get("link", "No Link")
        summary = entry.get("summary", "No Summary Available.")
        published = entry.get("published", "No Date Provided")
        author = entry.get("author") or entry.get("dc_creator", "Unknown Author")
        tags = [t.term for t in entry.get("tags", [])] if "tags" in entry else []
        tags_str = ", ".join(tags) if tags else "None"
        image_url = "No Image Available"
        if "media_content" in entry and len(entry.media_content) > 0:
            image_url = entry.media_content[0].get("url", image_url)
        newsX.append({
            "author" : author,
            "dated" : published,
            "summary" : summary,
            "keywords" : tags_str.split(','),
            "image" : image_url,
            "link" : link,
            "title" : title
        })
    return newsX
