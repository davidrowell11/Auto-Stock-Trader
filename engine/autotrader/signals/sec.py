"""SEC EDGAR data (free): insider buying and red-flag filings.

The SEC asks every program to identify itself with a contact email, set via
``AUTOTRADER_SEC_CONTACT`` (for example ``you@example.com``). EDGAR allows
up to 10 requests a second; this client stays well under that.
"""

from __future__ import annotations

import os
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from .http import RateLimited

TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
ARCHIVE_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{accession}/{doc}"

# Filings that usually mean new shares are coming (dilution).
DILUTION_FORMS = {"S-1", "S-1/A", "S-3", "S-3/A", "424B1", "424B3", "424B4", "424B5"}
LATE_FORMS = {"NT 10-K", "NT 10-Q", "NT 20-F"}
PERIODIC_FORMS = {"10-K", "10-Q", "20-F", "40-F", "10-K/A", "10-Q/A"}


@dataclass
class Filing:
    form: str
    filed: date
    accession: str
    primary_doc: str


@dataclass
class InsiderActivity:
    buyers: list[str] = field(default_factory=list)  # distinct insiders who bought
    total_value: float = 0.0  # dollars bought on the open market
    since: date | None = None

    @property
    def summary(self) -> str:
        if not self.buyers:
            return "No insider open-market buying"
        return (f"{len(self.buyers)} insider(s) bought ${self.total_value:,.0f} "
                f"on the open market since {self.since}")


class SecClient:
    def __init__(self, contact: str | None = None, fetch: RateLimited | None = None,
                 today: date | None = None) -> None:
        contact = contact or os.environ.get("AUTOTRADER_SEC_CONTACT", "")
        self.headers = {"User-Agent": f"AutoStockTrader {contact}".strip(),
                        "Accept-Encoding": "identity"}
        self.fetch = fetch or RateLimited()
        self.today = today
        self._ciks: dict[str, int] | None = None
        self._ciks_at = 0.0
        self._filings: dict[str, tuple[float, list[Filing]]] = {}

    # The server runs for weeks, so cached filings are refreshed regularly.
    FILINGS_TTL = 6 * 3600
    TICKERS_TTL = 7 * 24 * 3600

    def _today(self) -> date:
        return self.today or date.today()

    def cik(self, symbol: str) -> int | None:
        if self._ciks is None or time.monotonic() - self._ciks_at > self.TICKERS_TTL:
            data = self.fetch.json(TICKERS_URL, self.headers)
            self._ciks = {row["ticker"].upper(): int(row["cik_str"]) for row in data.values()}
            self._ciks_at = time.monotonic()
        return self._ciks.get(symbol.upper().replace(".", "-"))

    def filings(self, symbol: str) -> list[Filing]:
        """Recent filings for a company, newest first (cached for a few hours)."""
        cached = self._filings.get(symbol)
        if cached and time.monotonic() - cached[0] < self.FILINGS_TTL:
            return cached[1]
        cik = self.cik(symbol)
        if cik is None:
            return []
        recent = self.fetch.json(SUBMISSIONS_URL.format(cik=cik), self.headers)["filings"]["recent"]
        out = [
            Filing(form, datetime.strptime(filed, "%Y-%m-%d").date(), acc, doc)
            for form, filed, acc, doc in zip(
                recent["form"], recent["filingDate"], recent["accessionNumber"],
                recent["primaryDocument"],
            )
        ]
        self._filings[symbol] = (time.monotonic(), out)
        return out

    # ----- insider buying (Form 4) -----
    def insider_buying(self, symbol: str, days: int = 90, max_filings: int = 25) -> InsiderActivity:
        since = self._today() - timedelta(days=days)
        activity = InsiderActivity(since=since)
        cik = self.cik(symbol)
        if cik is None:
            return activity
        form4s = [f for f in self.filings(symbol) if f.form == "4" and f.filed >= since]
        buyers: set[str] = set()
        for f in form4s[:max_filings]:
            doc = f.primary_doc.rsplit("/", 1)[-1]  # drop the "xslF345X05/" viewer prefix
            url = ARCHIVE_URL.format(cik=cik, accession=f.accession.replace("-", ""), doc=doc)
            try:
                name, value = parse_form4_purchases(self.fetch(url, self.headers))
            except (ET.ParseError, OSError):
                continue
            if value > 0:
                buyers.add(name)
                activity.total_value += value
        activity.buyers = sorted(buyers)
        return activity

    # ----- red flags for penny stocks -----
    def penny_filings_check(self, symbol: str) -> tuple[bool, str]:
        """Fails closed: if the company can't be checked, the bot doesn't buy."""
        try:
            filings = self.filings(symbol)
        except Exception as exc:  # network, rate limit page, odd JSON: all fail closed
            return False, f"Couldn't check SEC filings ({exc})"
        if not filings:
            return False, "No SEC filings found, so the company can't be checked"
        today = self._today()
        dilution = [f for f in filings if f.form in DILUTION_FORMS and f.filed >= today - timedelta(days=90)]
        if dilution:
            return False, f"Filed {dilution[0].form} on {dilution[0].filed}: likely selling new shares (dilution)"
        late = [f for f in filings if f.form in LATE_FORMS and f.filed >= today - timedelta(days=365)]
        if late:
            return False, f"Filed {late[0].form} on {late[0].filed}: reports were late"
        periodic = [f for f in filings if f.form in PERIODIC_FORMS]
        if not periodic or periodic[0].filed < today - timedelta(days=200):
            return False, "No quarterly or annual report in over 6 months"
        return True, "SEC filings look normal"


def parse_form4_purchases(xml_bytes: bytes) -> tuple[str, float]:
    """Return (insider name, dollars of open-market purchases) from a Form 4 XML."""
    root = ET.fromstring(xml_bytes)
    name = (root.findtext("reportingOwner/reportingOwnerId/rptOwnerName") or "Unknown").strip()
    total = 0.0
    for tx in root.iter("nonDerivativeTransaction"):
        code = (tx.findtext("transactionCoding/transactionCode") or "").strip()
        acquired = (tx.findtext("transactionAmounts/transactionAcquiredDisposedCode/value") or "").strip()
        if code != "P" or acquired != "A":  # P = open-market purchase
            continue
        try:
            shares = float(tx.findtext("transactionAmounts/transactionShares/value") or 0)
            price = float(tx.findtext("transactionAmounts/transactionPricePerShare/value") or 0)
        except ValueError:
            continue
        total += shares * price
    return name, total
