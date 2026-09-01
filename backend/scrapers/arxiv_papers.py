"""
arXiv recent papers scraper — replacing google scholar to provide daily/weekly/monthly/yearly filterable CS/ML papers.
"""
import httpx
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta

def build_arxiv_query() -> str:
    # We query AI, CV, CL, LG (machine learning and AI related categories)
    return "cat:cs.AI+OR+cat:cs.CL+OR+cat:cs.CV+OR+cat:cs.LG"

async def fetch(period: str = "daily", limit: int = 200) -> list[dict]:
    days = {"daily": 3, "weekly": 7, "monthly": 30, "halfyear": 180}.get(period, 3) 
    
    # Fetch a large batch to ensure we have enough valid papers after date filtering
    url = f"http://export.arxiv.org/api/query?search_query={build_arxiv_query()}&sortBy=submittedDate&sortOrder=descending&max_results=500"
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    }
    
    items = []
    try:
        async with httpx.AsyncClient(timeout=30, headers=headers, follow_redirects=True) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            
            root = ET.fromstring(resp.text)
            ns = {'atom': 'http://www.w3.org/2005/Atom'}
            
            base_date = datetime.utcnow()
            cutoff_date = base_date - timedelta(days=days)
            
            for entry in root.findall('atom:entry', ns):
                title = entry.find('atom:title', ns).text.strip().replace('\n', ' ')
                summary = entry.find('atom:summary', ns).text.strip().replace('\n', ' ')
                link = entry.find('atom:id', ns).text.strip()
                published_text = entry.find('atom:published', ns).text.strip()
                
                # Strict date filtering
                try:
                    pub_date = datetime.strptime(published_text[:10], "%Y-%m-%d")
                    if pub_date < cutoff_date:
                        continue # Skip this paper entirely if it's too old
                except:
                    pass
                
                authors = [author.find('atom:name', ns).text for author in entry.findall('atom:author', ns)]
                year = published_text[:4] if published_text else ""
                
                items.append({
                    "title": title,
                    "url": link,
                    "abstract": summary,
                    "authors": authors,
                    "year": year,
                    "citations": 0 # arXiv doesn't provide citations directly on RSS
                })
                
                if len(items) >= limit:
                    break # Stop once we have reached the desired limit of valid papers
    except Exception as e:
        print(f"ArXiv fetch error: {e}")
        
    return items
