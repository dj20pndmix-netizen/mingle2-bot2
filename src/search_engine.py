"""
Mingle2 Bot — Search Engine & Deep Profession Ranking
Provides deep semantic profession expansion, explainable multi-signal ranking,
filtering (location, age, keywords, completeness), and duplicate elimination.
Strictly adheres to policy: No filtering on sensitive characteristics.
"""

from dataclasses import dataclass, field
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("mingle2.search_engine")

# Profession taxonomy mapping canonical professions to synonyms, roles, and relevance weights
PROFESSION_TAXONOMY: Dict[str, Dict[str, float]] = {
    "nurse": {
        "registered nurse": 0.98,
        "nurse": 0.95,
        "rn": 0.95,
        "nursing": 0.90,
        "registered nursing": 0.95,
        "nurse practitioner": 0.96,
        "clinical nurse": 0.95,
        "pediatric nurse": 0.95,
        "icu nurse": 0.95,
        "lpn": 0.92,
        "nursing student": 0.82,
        "student nurse": 0.82,
        "caregiver": 0.70,
        "healthcare": 0.65,
    },
    "teacher": {
        "teacher": 0.95,
        "educator": 0.95,
        "instructor": 0.92,
        "professor": 0.95,
        "teaching": 0.90,
        "high school teacher": 0.98,
        "primary teacher": 0.98,
        "lecturer": 0.94,
        "tutor": 0.85,
        "education": 0.75,
    },
    "doctor": {
        "medical doctor": 0.98,
        "physician": 0.98,
        "doctor": 0.95,
        "md": 0.95,
        "surgeon": 0.97,
        "general practitioner": 0.96,
        "pediatrician": 0.96,
        "resident doctor": 0.90,
        "medical student": 0.80,
        "medicine": 0.75,
    },
    "engineer": {
        "software engineer": 0.98,
        "civil engineer": 0.98,
        "mechanical engineer": 0.98,
        "electrical engineer": 0.98,
        "engineer": 0.95,
        "engineering": 0.90,
        "developer": 0.92,
        "software developer": 0.96,
        "programmer": 0.90,
        "engineering student": 0.82,
    },
    "accountant": {
        "certified public accountant": 0.98,
        "cpa": 0.98,
        "accountant": 0.95,
        "chartered accountant": 0.98,
        "accounting": 0.90,
        "auditor": 0.94,
        "bookkeeper": 0.88,
        "financial analyst": 0.85,
        "finance": 0.75,
    },
    "lawyer": {
        "attorney": 0.98,
        "lawyer": 0.95,
        "advocate": 0.95,
        "counsel": 0.92,
        "legal counsel": 0.96,
        "legal advisor": 0.94,
        "law student": 0.80,
    },
}


@dataclass
class SearchResult:
    """Ranked profile result with transparent and explainable match scoring."""
    platform_profile_id: str
    display_name: str
    age: Optional[int]
    location: str
    profession: str
    profile_url: str
    photo_reference: Optional[str]
    headline: str = ""
    about_me: str = ""
    interests: List[str] = field(default_factory=list)
    is_online: bool = False

    # Scoring signals (0-100)
    profession_score: int = 0
    location_score: int = 0
    keyword_score: int = 0
    completeness_score: int = 0
    total_match_score: int = 0
    score_breakdown: str = ""
    detected_profession_match: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "platform_profile_id": self.platform_profile_id,
            "display_name": self.display_name,
            "age": self.age,
            "location": self.location,
            "profession": self.profession,
            "profile_url": self.profile_url,
            "photo_reference": self.photo_reference,
            "headline": self.headline,
            "about_me": self.about_me,
            "interests": self.interests,
            "is_online": self.is_online,
            "profession_score": self.profession_score,
            "location_score": self.location_score,
            "keyword_score": self.keyword_score,
            "completeness_score": self.completeness_score,
            "total_match_score": self.total_match_score,
            "score_breakdown": self.score_breakdown,
            "detected_profession_match": self.detected_profession_match,
        }


def extract_profession_from_profile_text(text: str) -> Optional[Tuple[str, float, str]]:
    """
    Scans profile text (headline, bio, details, occupation) to identify legitimate professions.
    Returns (matched_category, relevance_weight, matched_term) or None.
    Does not fabricate or guess without textual evidence.
    """
    if not text:
        return None

    cleaned_text = text.lower()

    # Check taxonomy matches
    for canonical, terms_map in PROFESSION_TAXONOMY.items():
        # Sort terms by length descending so multi-word matches (e.g. "registered nurse") take precedence
        sorted_terms = sorted(terms_map.keys(), key=lambda t: len(t), reverse=True)
        for term in sorted_terms:
            pattern = rf"\b{re.escape(term)}\b"
            if re.search(pattern, cleaned_text):
                weight = terms_map[term]
                return canonical, weight, term.title()

    return None


def calculate_match_scores(
    profile_data: Dict[str, Any],
    query_profession: Optional[str] = None,
    query_location: Optional[str] = None,
    query_keywords: Optional[str] = None,
    age_min: Optional[int] = None,
    age_max: Optional[int] = None,
) -> Tuple[int, int, int, int, int, str, Optional[str]]:
    """
    Calculates explainable match scores for a profile against search criteria.
    Returns:
        (total_score, profession_score, location_score, keyword_score, completeness_score, breakdown_str, matched_term)
    """
    headline = str(profile_data.get("headline", "")).strip()
    about_me = str(profile_data.get("about_me", "")).strip()
    loc = str(profile_data.get("location") or profile_data.get("age_location", "")).strip()
    username = str(profile_data.get("username") or profile_data.get("display_name", "")).strip()
    all_text = f"{headline} {about_me} {loc} {username}".lower()

    profession_score = 0
    matched_term = None
    if query_profession:
        q_prof = query_profession.strip().lower()
        # Direct match or taxonomy search
        taxonomy_terms = PROFESSION_TAXONOMY.get(q_prof)
        if taxonomy_terms:
            sorted_terms = sorted(taxonomy_terms.keys(), key=lambda t: len(t), reverse=True)
            for term in sorted_terms:
                if re.search(rf"\b{re.escape(term)}\b", all_text):
                    profession_score = int(taxonomy_terms[term] * 100)
                    matched_term = term.title()
                    break
        else:
            # Custom profession term
            if re.search(rf"\b{re.escape(q_prof)}\b", all_text):
                profession_score = 95
                matched_term = q_prof.title()
            elif q_prof in all_text:
                profession_score = 80
                matched_term = q_prof.title()

    location_score = 100
    if query_location:
        q_loc = query_location.strip().lower()
        if q_loc in loc.lower():
            location_score = 100
        else:
            # Check partial tokens
            tokens = [t for t in re.split(r"[\s,]+", q_loc) if len(t) > 2]
            if tokens and any(t in loc.lower() for t in tokens):
                location_score = 75
            else:
                location_score = 20

    keyword_score = 100
    if query_keywords:
        kw_list = [k.strip().lower() for k in re.split(r"[\s,]+", query_keywords) if k.strip()]
        if kw_list:
            matched_kws = sum(1 for k in kw_list if k in all_text)
            if matched_kws > 0:
                # Generous scoring for synonym matching: base 75% for finding at least one requested keyword
                keyword_score = min(100, 75 + int((matched_kws / len(kw_list)) * 25))
            else:
                keyword_score = 0

    # Profile completeness score
    completeness_pts = 0
    if profile_data.get("avatar_url") or profile_data.get("photo_reference"):
        completeness_pts += 35
    if headline:
        completeness_pts += 20
    if about_me and len(about_me) > 20:
        completeness_pts += 25
    if loc:
        completeness_pts += 10
    if profile_data.get("age"):
        completeness_pts += 10
    completeness_score = min(100, completeness_pts)

    # Weighted Total Score
    # If profession requested, it has 50% weight; location 25%; keywords 15%; completeness 10%
    if query_profession:
        total = int((profession_score * 0.50) + (location_score * 0.25) + (keyword_score * 0.15) + (completeness_score * 0.10))
    else:
        total = int((location_score * 0.45) + (keyword_score * 0.35) + (completeness_score * 0.20))

    breakdown = []
    if query_profession:
        breakdown.append(f"Profession: {profession_score}% ({matched_term or 'No match'})")
    if query_location:
        breakdown.append(f"Location: {location_score}%")
    if query_keywords:
        breakdown.append(f"Keywords: {keyword_score}%")
    breakdown.append(f"Completeness: {completeness_score}%")
    breakdown_str = " | ".join(breakdown)

    return total, profession_score, location_score, keyword_score, completeness_score, breakdown_str, matched_term


def rank_and_filter_search_results(
    candidates: List[Dict[str, Any]],
    query_profession: Optional[str] = None,
    query_location: Optional[str] = None,
    query_keywords: Optional[str] = None,
    age_min: Optional[int] = None,
    age_max: Optional[int] = None,
    min_match_threshold: int = 30,
) -> List[SearchResult]:
    """
    Ranks, scores, filters, and deduplicates search candidate profiles.
    Returns ordered list of SearchResult items sorted by total_match_score descending.
    """
    seen_ids = set()
    ranked_results: List[SearchResult] = []

    for c in candidates:
        raw_uid = str(c.get("platform_profile_id") or c.get("user_id") or c.get("id") or "").strip()
        if not raw_uid or raw_uid in seen_ids:
            continue

        # Extract and check age filter if specified
        age = c.get("age")
        if age is None:
            age_loc = str(c.get("location") or c.get("age_location", ""))
            m = re.search(r"(?:Age:?\s*|,\s*|^)(\d{2})(?:\s*•|,|$|\s)", age_loc, re.IGNORECASE)
            if m:
                try:
                    age = int(m.group(1))
                except Exception:
                    pass

        if age_min and age and age < age_min:
            continue
        if age_max and age and age > age_max:
            continue

        total, prof_sc, loc_sc, kw_sc, comp_sc, breakdown_str, matched_term = calculate_match_scores(
            c,
            query_profession=query_profession,
            query_location=query_location,
            query_keywords=query_keywords,
            age_min=age_min,
            age_max=age_max,
        )

        # If user searched for a profession specifically and candidate scored 0 on it, filter out
        if query_profession and prof_sc == 0:
            continue

        if total < min_match_threshold:
            continue

        seen_ids.add(raw_uid)

        # Infer or preserve profession
        detected_prof = c.get("profession") or matched_term or ""

        url = c.get("profile_url") or f"https://mingle2.com/users/view/{raw_uid}"
        sr = SearchResult(
            platform_profile_id=raw_uid,
            display_name=str(c.get("username") or c.get("display_name") or "Mingle2 Member"),
            age=age,
            location=str(c.get("location") or c.get("age_location", "")),
            profession=detected_prof,
            profile_url=url,
            photo_reference=c.get("avatar_url") or c.get("avatar_img_url"),
            headline=str(c.get("headline", "")),
            about_me=str(c.get("about_me", "")),
            is_online=bool(c.get("is_online", False)),
            profession_score=prof_sc,
            location_score=loc_sc,
            keyword_score=kw_sc,
            completeness_score=comp_sc,
            total_match_score=total,
            score_breakdown=breakdown_str,
            detected_profession_match=matched_term,
        )
        ranked_results.append(sr)

    # Sort descending by total score, then profession score, then completeness
    ranked_results.sort(
        key=lambda r: (r.total_match_score, r.profession_score, r.completeness_score),
        reverse=True,
    )
    return ranked_results
