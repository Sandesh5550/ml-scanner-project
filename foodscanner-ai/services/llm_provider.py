from __future__ import annotations

from abc import ABC, abstractmethod
import json
import logging
import os
import re
import time
from typing import Any, Dict, List, Optional
import requests

logger = logging.getLogger(__name__)


class LLMProviderError(RuntimeError):
    """Base exception for LLM provider errors."""
    pass


class LLMConfigurationError(LLMProviderError):
    """Raised when LLM provider is misconfigured or lacks necessary credentials."""
    pass


class LLMUnavailableError(LLMProviderError):
    """Raised when the LLM service times out or cannot be reached."""
    pass


def is_prompt_injection_or_leak_attempt(query: str) -> bool:
    """Detect common prompt-injection, jailbreak, and system-prompt extraction patterns."""
    q = (query or "").lower().strip()
    if not q:
        return False

    patterns = [
        r"\b(ignore|forget|disregard|override|bypass)\s+(all\s+)?(previous|prior|above|system|retrieved)?\s*(instructions|prompts|rules|directives|context|guidelines)",
        r"\b(forget|disregard|ignore)\s+(the\s+)?(retrieved\s+)?context\b",
        r"\b(show|tell|reveal|display|output|print|what\s+is)\s+(the\s+|your\s+)?(hidden\s+|secret\s+)?(system\s+prompt|initial\s+prompt|instructions|developer\s+mode|internal\s+rules|api\s*key|secret)",
        r"\b(you\s+are\s+now\s+in\s+developer\s+mode|jailbreak|dan\s+mode|unrestricted\s+mode|bypass\s+all\s+safety)",
        r"\b(give\s+me|generate|create)\s+(a\s+)?(fake|false|bogus)\s+(fssai|approval|certification|license)",
        r"\b(tell\s+me\s+the\s+hidden\s+system\s+prompt)",
        r"\b(give\s+me\s+information\s+even\s+if\s+it\s+is\s+not\s+in\s+the\s+knowledge\s+base)",
    ]
    for pat in patterns:
        if re.search(pat, q):
            return True
    return False


def is_score_calculation_or_override_request(query: str) -> bool:
    """Detect user requests asking the LLM to independently compute, change, or invent a health score."""
    q = (query or "").lower().strip()
    if not q:
        return False

    patterns = [
        r"\b(calculate|compute|generate|give|assign|recalculate)\s+(the\s+)?(health\s+score|score)\s+(yourself|independently|without\s+backend|on\s+your\s+own)",
        r"\b(override|change|modify|fake|set|make)\s+(the\s+)?(health\s+score|score).*\bto\s+\d+",
        r"\b(recalculate|re-calculate)\s+(and\s+change\s+)?(the\s+)?(health\s+score|score)",
        r"\b(can\s+you\s+calculate\s+the\s+health\s+score\s+yourself)",
        r"\b(calculate\s+the\s+score\s+yourself)",
        r"\b(recalculate\s+the\s+score\s+without\s+the\s+backend)",
    ]
    for pat in patterns:
        if re.search(pat, q):
            return True
    return False


def is_unsupported_regulatory_or_medical_claim(query: str) -> bool:
    """Detect queries asking if FSSAI certifies or approves packaged foods to cure diseases or making unsupported medicinal claims."""
    q = (query or "").lower().strip()
    if not q:
        return False
    patterns = [
        r"\b(cure|curing|treat|treating|prevent|preventing)\s+(heart\s+disease|cancer|diabetes|stroke|hypertension)\b",
        r"\bfssai\s+(certif\w+|approv\w+).*(cure|curing|treat|medicinal)",
        r"\b(can|does)\s+fssai\s+(certify|approve).*(cure|treat|prevent)",
        r"\b(cure|cures|cured)\s+(for|of)\s+cancer\b",
    ]
    for pat in patterns:
        if re.search(pat, q):
            return True
    return False


class BaseLLMProvider(ABC):
    """Abstract base class for AI LLM providers."""

    @abstractmethod
    def generate(
        self,
        system_prompt: str,
        user_prompt: str,
        context_data: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Generate a grounded response given system prompt and user prompt."""
        pass

    @abstractmethod
    def health_check(self) -> Dict[str, Any]:
        """Check if the provider is configured and available."""
        pass


class MockLLMProvider(BaseLLMProvider):
    """Deterministic, grounded mock LLM provider for offline testing and reliable presentation demos.

    Constructs realistic, high-quality, structured responses grounded entirely in
    the provided product facts, score factors, and retrieved knowledge sources.
    Strictly defends against prompt injection and health-score tampering.
    """

    def health_check(self) -> Dict[str, Any]:
        return {"status": "available", "provider": "mock", "model": "deterministic-mock-v1"}

    def generate(
        self,
        system_prompt: str,
        user_prompt: str,
        context_data: Optional[Dict[str, Any]] = None,
    ) -> str:
        data = context_data or {}
        query = str(data.get("query") or user_prompt).strip()
        query_lower = query.lower()

        # 1. Guardrail: Prompt Injection & System Prompt Leak Defense
        if is_prompt_injection_or_leak_attempt(query):
            return (
                "I am the PRAMAAN AI Nutrition Assistant. I adhere strictly to application security guidelines "
                "and cannot reveal internal system instructions, execute meta-directives, or override nutritional safety policies."
            )

        # 2. Guardrail: Refuse Independent Health Score Calculation
        if is_score_calculation_or_override_request(query):
            return (
                "PRAMAAN's Health Score is strictly generated by the deterministic backend health-assessment system "
                "based on declared nutritional facts, additives, and statutory FSSAI/WHO guidelines. As an AI assistant, "
                "I cannot independently calculate, modify, or invent a numerical health score."
            )

        # 3. Guardrail: Refuse Unsupported Regulatory or Medical Disease-Cure Claims
        if is_unsupported_regulatory_or_medical_claim(query):
            return (
                "FSSAI does not certify or approve packaged foods to cure, treat, or prevent diseases. "
                "Under statutory FSSAI Advertising and Claims Regulations, packaged foods are strictly prohibited from "
                "making medicinal disease-cure claims. Sufficient verified evidence was not found for this claim."
            )

        product = data.get("product")
        user_profile = data.get("user_profile") or {}
        sources = data.get("sources") or []

        p_name = product.get("product_name") or product.get("name") if product else None
        p_score = product.get("health_score") if product else None
        p_decision = product.get("final_decision") if product else None
        nutr = (product.get("nutrition") or {}) if product else {}

        goal_type = (user_profile.get("goal_type") or "").lower()
        diet_type = (user_profile.get("diet_type") or "").lower()

        # Build structured response components
        sections: List[str] = []

        # Case 1: General question (No product context)
        if not product:
            if "sugar" in query_lower and "added" in query_lower:
                sections.append(
                    "Total sugar includes naturally occurring sugars (like lactose in milk or fructose in fruit) "
                    "plus added refined sugars. Added sugars contribute empty calories without vitamins or fiber, "
                    "whereas naturally occurring sugars come with water and micronutrients."
                )
                sections.append(
                    "\nWhy it matters:\n"
                    "• WHO guidelines recommend limiting free/added sugars to under 10% of daily calories (approx 50g), ideally under 25g.\n"
                    "• Added sugars rapidly elevate blood glucose and increase risks of dental caries and metabolic disorders."
                )
            elif "score" in query_lower or "pramaan" in query_lower:
                sections.append(
                    "PRAMAAN calculates an objective, deterministic 0–100 health score starting from a baseline of 100. "
                    "Penalties are deducted for high sugar, salt, saturated fat, excess calories, and risky additives, "
                    "while bonuses are awarded for dietary fiber and protein. Thresholds: SAFE (>= 70), MODERATE (45–69), AVOID (< 45)."
                )
            elif "protein" in query_lower:
                sections.append(
                    "According to ICMR-NIN 2020 guidelines, healthy Indian adults require approximately 0.83g of protein "
                    "per kg body weight (around 54g/day for a 65kg adult). Active individuals and those building muscle benefit from 1.2–2.0g/kg."
                )
            elif "weight loss" in query_lower or "lose weight" in query_lower:
                sections.append(
                    "For healthy weight management, prioritize foods with low caloric density (< 200 kcal/100g), "
                    "high dietary fiber (> 3g/100g), and lean protein (> 8g/100g) to maximize fullness while staying within your daily calorie limit."
                )
            elif "salt" in query_lower or "sodium" in query_lower:
                sections.append(
                    "WHO guidelines recommend consuming less than 2000 mg of sodium per day (equivalent to less than 5g of table salt). "
                    "Packaged snacks with salt content exceeding 1.5g per 100g are considered high sodium."
                )
            elif sources:
                # Use retrieved source topic
                top_s = sources[0]
                sections.append(f"{top_s.get('text')}")
            else:
                # Grounded evidence absence notice
                sections.append(
                    "Sufficient verified evidence was not found in the local knowledge base for this query. "
                    "The PRAMAAN AI Assistant provides factual nutrition information strictly grounded in verified FSSAI, ICMR-NIN, and WHO guidelines."
                )

            if sources:
                src_names = [f"• {s.get('source')} ({s.get('title') or s.get('topic')})" for s in sources[:2]]
                sections.append("\nSources:\n" + "\n".join(src_names))
            return "\n".join(sections)

        # Case 2: Product-specific questions
        # Subcase 2A: Score explanation query
        if "why" in query_lower and ("score" in query_lower or "get" in query_lower):
            score_str = f"{p_score}/100" if p_score is not None else "evaluated"
            dec_str = f" ({p_decision})" if p_decision else ""
            sections.append(f"PRAMAAN's official score for {p_name} is {score_str}{dec_str}.")

            why_bullets = []
            reasons = product.get("reasons") or []
            if reasons:
                for r in reasons[:3]:
                    why_bullets.append(f"• {r}")
            else:
                sugar_val = nutr.get("sugar")
                fat_val = nutr.get("fat")
                prot_val = nutr.get("protein")
                if sugar_val is not None and sugar_val > 15:
                    why_bullets.append(f"• High sugar content ({sugar_val}g/100g) resulted in a score deduction.")
                if fat_val is not None and fat_val > 20:
                    why_bullets.append(f"• High fat content ({fat_val}g/100g) affected the overall score.")
                if prot_val is not None and prot_val >= 8:
                    why_bullets.append(f"• Protein content ({prot_val}g/100g) contributed positive bonus points.")

            if not why_bullets:
                why_bullets.append("• Evaluated against FSSAI and WHO nutritional thresholds.")
            sections.append("\nWhy:\n" + "\n".join(why_bullets))

        # Subcase 2B: Alternative query
        elif "alternative" in query_lower or "healthier" in query_lower or "substitute" in query_lower:
            alts = product.get("recommendations") or []
            if alts:
                top_alt = alts[0]
                alt_name = top_alt.get("product_name", "Healthier alternative")
                alt_reason = top_alt.get("reason", "Better balanced nutritional profile")
                alt_score = top_alt.get("health_score")
                score_part = f" (Score: {alt_score}/100)" if alt_score else ""
                sections.append(f"A healthier alternative in this category is {alt_name}{score_part}.")
                sections.append(f"\nWhy:\n• {alt_reason}")
            else:
                sections.append(f"No significantly healthier packaged alternative was found for {p_name} in its category.")

        # Subcase 2C: Weight loss fit
        elif "weight loss" in query_lower or "lose weight" in query_lower:
            cal_val = nutr.get("calories")
            sug_val = nutr.get("sugar")
            fib_val = nutr.get("fiber")

            cal_str = f"{cal_val} kcal/100g" if cal_val is not None else "Calorie data not available"
            is_high_cal = cal_val is not None and cal_val > 300
            is_high_sug = sug_val is not None and sug_val > 15

            if is_high_cal or is_high_sug:
                sections.append(f"{p_name} is not ideal for weight loss due to its high caloric density ({cal_str}).")
            else:
                sections.append(f"{p_name} can fit into a weight loss plan in moderation ({cal_str}).")

            why_bullets = []
            if cal_val is not None:
                why_bullets.append(f"• Provides {cal_val} kcal per 100g towards your daily calorie budget.")
            if sug_val is not None:
                why_bullets.append(f"• Sugar content is {sug_val}g/100g.")
            if fib_val is not None:
                why_bullets.append(f"• Dietary fibre is {fib_val}g/100g (higher fiber supports satiety).")
            sections.append("\nWhy:\n" + "\n".join(why_bullets))

        # Subcase 2D: Muscle building query
        elif "muscle" in query_lower or "protein" in query_lower:
            prot_val = nutr.get("protein")
            prot_str = f"{prot_val}g/100g" if prot_val is not None else "Protein value not available on label"
            if prot_val is not None and prot_val >= 10:
                sections.append(f"{p_name} provides a good amount of protein ({prot_str}) to support muscle synthesis.")
            else:
                sections.append(f"{p_name} is relatively low in protein ({prot_str}) for a dedicated muscle building target.")

            why_bullets = [f"• Declared protein: {prot_str}"]
            cal_val = nutr.get("calories")
            if cal_val is not None:
                why_bullets.append(f"• Energy: {cal_val} kcal/100g")
            sections.append("\nWhy:\n" + "\n".join(why_bullets))

        # Subcase 2E: General product evaluation
        else:
            score_disp = f"PRAMAAN Health Score is {p_score}/100 ({p_decision})" if p_score is not None else "Analyzed nutritional profile"
            sections.append(f"Here is the evaluation for {p_name}: {score_disp}.")

            why_bullets = []
            for k, label, unit in [
                ("calories", "Calories", "kcal"),
                ("protein", "Protein", "g"),
                ("sugar", "Sugar", "g"),
                ("fat", "Fat", "g"),
                ("salt", "Salt/Sodium", "g"),
                ("fiber", "Fibre", "g"),
            ]:
                v = nutr.get(k)
                if v is not None:
                    why_bullets.append(f"• {label}: {v} {unit}/100g")
                else:
                    why_bullets.append(f"• {label}: Not available on label")
            sections.append("\nWhy:\n" + "\n".join(why_bullets[:4]))

        # Section: For your goal / diet (if applicable)
        if goal_type:
            goal_clean = goal_type.replace('_', ' ')
            sections.append(f"\nFor your goal ({goal_clean}):\n• Keep portion sizes mindful to stay aligned with your daily nutrition targets.")
        if diet_type:
            diet_clean = diet_type.replace('_', ' ')
            diet_note = product.get("diet_note")
            if diet_note:
                sections.append(f"\nFor your diet ({diet_clean}):\n• {diet_note}")

        # Section: Sources
        if sources:
            src_lines = [f"• {s.get('source')} ({s.get('title') or s.get('topic')})" for s in sources[:2]]
            sections.append("\nSources:\n" + "\n".join(src_lines))

        return "\n".join(sections)


class GeminiProvider(BaseLLMProvider):
    """Google Gemini API Provider with bounded retries and credential redaction."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: str = "gemini-1.5-flash",
        timeout: float = 12.0,
        max_retries: int = 2,
    ):
        self.api_key = api_key or os.environ.get("AI_API_KEY")
        self.model = model or os.environ.get("AI_MODEL", "gemini-1.5-flash")
        self.timeout = timeout
        self.max_retries = max(1, min(3, int(max_retries)))

    def health_check(self) -> Dict[str, Any]:
        if not self.api_key:
            return {"status": "unconfigured", "provider": "gemini", "error": "AI_API_KEY is not set"}
        return {"status": "configured", "provider": "gemini", "model": self.model}

    def generate(
        self,
        system_prompt: str,
        user_prompt: str,
        context_data: Optional[Dict[str, Any]] = None,
    ) -> str:
        if not self.api_key:
            raise LLMConfigurationError("Gemini AI_API_KEY is missing")

        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent?key={self.api_key}"
        payload = {
            "contents": [
                {"role": "user", "parts": [{"text": f"{system_prompt}\n\n{user_prompt}"}]}
            ],
            "generationConfig": {
                "temperature": 0.2,
                "maxOutputTokens": 600,
            },
        }

        last_error: Optional[Exception] = None
        for attempt in range(self.max_retries):
            try:
                resp = requests.post(url, json=payload, timeout=self.timeout)
                if resp.status_code == 200:
                    data = resp.json()
                    candidates = data.get("candidates") or []
                    if not candidates:
                        raise LLMProviderError("Empty candidates returned from Gemini API")
                    parts = candidates[0].get("content", {}).get("parts", [])
                    if not parts:
                        raise LLMProviderError("Malformed content in Gemini API response")
                    return str(parts[0].get("text", "")).strip()

                # Bounded retry on rate limit or transient service error
                if resp.status_code in (429, 503) and attempt < self.max_retries - 1:
                    time.sleep(0.5 * (attempt + 1))
                    continue

                # Never log raw response if it might leak API key
                raise LLMProviderError(f"Gemini API returned HTTP {resp.status_code}")

            except requests.exceptions.Timeout as exc:
                last_error = exc
                if attempt < self.max_retries - 1:
                    time.sleep(0.5)
                    continue
                raise LLMUnavailableError(f"Gemini API timed out after {self.timeout}s") from exc

            except requests.exceptions.RequestException as exc:
                last_error = exc
                if attempt < self.max_retries - 1:
                    time.sleep(0.5)
                    continue
                # Sanitize error message to prevent accidental key exposure
                safe_msg = re.sub(r"key=[^&\s]+", "key=***", str(exc))
                raise LLMUnavailableError(f"Gemini network request failed: {safe_msg}") from exc

        if last_error:
            raise LLMUnavailableError(f"Gemini API call failed after {self.max_retries} attempts: {last_error}")
        raise LLMProviderError("Gemini API call failed without explicit error.")


class OpenAIProvider(BaseLLMProvider):
    """OpenAI or OpenAI-compatible (Ollama/LocalAI) API Provider with bounded retries."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: str = "gpt-4o-mini",
        base_url: str = "https://api.openai.com/v1",
        timeout: float = 12.0,
        max_retries: int = 2,
    ):
        self.api_key = api_key or os.environ.get("AI_API_KEY")
        self.model = model or os.environ.get("AI_MODEL", "gpt-4o-mini")
        self.base_url = os.environ.get("AI_BASE_URL", base_url).rstrip("/")
        self.timeout = timeout
        self.max_retries = max(1, min(3, int(max_retries)))

    def health_check(self) -> Dict[str, Any]:
        if not self.api_key:
            return {"status": "unconfigured", "provider": "openai", "error": "AI_API_KEY is not set"}
        return {"status": "configured", "provider": "openai", "model": self.model}

    def generate(
        self,
        system_prompt: str,
        user_prompt: str,
        context_data: Optional[Dict[str, Any]] = None,
    ) -> str:
        if not self.api_key:
            raise LLMConfigurationError("OpenAI AI_API_KEY is missing")

        url = f"{self.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.2,
            "max_tokens": 600,
        }

        last_error: Optional[Exception] = None
        for attempt in range(self.max_retries):
            try:
                resp = requests.post(url, headers=headers, json=payload, timeout=self.timeout)
                if resp.status_code == 200:
                    data = resp.json()
                    choices = data.get("choices") or []
                    if not choices:
                        raise LLMProviderError("Empty choices returned from OpenAI API")
                    msg = choices[0].get("message", {}).get("content", "")
                    return str(msg).strip()

                if resp.status_code in (429, 503) and attempt < self.max_retries - 1:
                    time.sleep(0.5 * (attempt + 1))
                    continue

                raise LLMProviderError(f"OpenAI API returned HTTP {resp.status_code}")

            except requests.exceptions.Timeout as exc:
                last_error = exc
                if attempt < self.max_retries - 1:
                    time.sleep(0.5)
                    continue
                raise LLMUnavailableError(f"OpenAI API timed out after {self.timeout}s") from exc

            except requests.exceptions.RequestException as exc:
                last_error = exc
                if attempt < self.max_retries - 1:
                    time.sleep(0.5)
                    continue
                raise LLMUnavailableError(f"OpenAI network request failed: {exc}") from exc

        if last_error:
            raise LLMUnavailableError(f"OpenAI API call failed after {self.max_retries} attempts: {last_error}")
        raise LLMProviderError("OpenAI API call failed without explicit error.")


def get_llm_provider() -> BaseLLMProvider:
    """Factory function to instantiate the configured LLM provider."""
    provider_name = (os.environ.get("AI_PROVIDER") or "mock").strip().lower()
    api_key = os.environ.get("AI_API_KEY")
    model = os.environ.get("AI_MODEL")

    if provider_name == "gemini":
        if not api_key:
            logger.warning("AI_PROVIDER=gemini requested but AI_API_KEY is empty; falling back to MockLLMProvider.")
            return MockLLMProvider()
        return GeminiProvider(api_key=api_key, model=model or "gemini-1.5-flash")

    elif provider_name in {"openai", "ollama"}:
        if not api_key and provider_name != "ollama":
            logger.warning("AI_PROVIDER=openai requested but AI_API_KEY is empty; falling back to MockLLMProvider.")
            return MockLLMProvider()
        return OpenAIProvider(api_key=api_key or "local-key", model=model or "gpt-4o-mini")

    # Default provider for offline testing and demos
    return MockLLMProvider()
