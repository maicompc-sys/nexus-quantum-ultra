"""
NEXUS QUANTUM ULTRA — Groq Client with 3-Key Round-Robin
Used EXCLUSIVELY for strategy analysis — never for trade execution.
Includes JSON extraction fix and rate-limit backoff.
"""

import asyncio
import json
import logging
import re
import time
from typing import Dict, Any, Optional

from groq import AsyncGroq

from utils.config import GROQ_KEYS, GROQ_MODEL_A, GROQ_MODEL_B, GROQ_MODEL_C
from utils.logger import agent_log


MODEL_ROSTER = [GROQ_MODEL_A, GROQ_MODEL_B, GROQ_MODEL_C]

# Rate limit tracking per key
_key_last_call: Dict[int, float] = {0: 0.0, 1: 0.0, 2: 0.0}
_key_errors:    Dict[int, int]   = {0: 0,   1: 0,   2: 0}
_MIN_GAP        = 1.2   # seconds between calls on same key


def _extract_json(text: str) -> Optional[Dict]:
    """
    Robustly extract JSON from model output.
    Handles markdown code blocks, mixed text, partial JSON.
    """
    if not text:
        return None

    # Try direct parse first
    try:
        return json.loads(text.strip())
    except Exception:
        pass

    # Find JSON block in markdown
    match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except Exception:
            pass

    # Find raw {...} block
    match = re.search(r"\{[^{}]*\}", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except Exception:
            pass

    return None


class GroqClient:
    def __init__(self):
        self._clients = [AsyncGroq(api_key=k) for k in GROQ_KEYS]
        self._current_key = 0

    def _next_key(self) -> int:
        """Round-robin key selection, skipping throttled keys."""
        start = self._current_key
        for _ in range(len(GROQ_KEYS)):
            idx = self._current_key % len(GROQ_KEYS)
            self._current_key = (self._current_key + 1) % len(GROQ_KEYS)

            now     = time.time()
            elapsed = now - _key_last_call[idx]
            errors  = _key_errors[idx]

            # Skip keys with too many recent errors
            if errors >= 3:
                backoff = min(60.0, 2 ** errors)
                if elapsed < backoff:
                    continue
                else:
                    _key_errors[idx] = 0   # reset after backoff

            if elapsed >= _MIN_GAP:
                return idx

        # All keys throttled — use least-recently-used
        return min(_key_last_call, key=_key_last_call.get)

    async def call(
        self,
        model: str,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int = 1024,
        temperature: float = 0.3,
        retries: int = 3,
    ) -> Dict[str, Any]:
        """
        Single model call with retry and JSON extraction.
        Returns: {content, model, key_idx, tokens, latency_ms, error}
        """
        last_error = ""
        for attempt in range(retries):
            key_idx = self._next_key()
            client  = self._clients[key_idx]
            t0      = time.time()

            try:
                # Enforce min gap
                elapsed = t0 - _key_last_call[key_idx]
                if elapsed < _MIN_GAP:
                    await asyncio.sleep(_MIN_GAP - elapsed)

                _key_last_call[key_idx] = time.time()

                resp = await client.chat.completions.create(
                    model=model,
                    messages=[
                        {"role": "system",  "content": system_prompt},
                        {"role": "user",    "content": user_prompt},
                    ],
                    max_tokens=max_tokens,
                    temperature=temperature,
                )

                raw_text  = resp.choices[0].message.content or ""
                tokens    = resp.usage.total_tokens if resp.usage else 0
                latency   = int((time.time() - t0) * 1000)

                parsed = _extract_json(raw_text)
                if parsed is None:
                    agent_log("COUNCIL", f"JSON não encontrado [{model}] raw={raw_text[:120]}", logging.WARNING)
                    parsed = {}

                _key_errors[key_idx] = max(0, _key_errors[key_idx] - 1)

                return {
                    "content":    parsed,
                    "raw":        raw_text,
                    "model":      model,
                    "key_idx":    key_idx,
                    "tokens":     tokens,
                    "latency_ms": latency,
                    "error":      None,
                }

            except Exception as e:
                last_error = str(e)
                _key_errors[key_idx] = _key_errors.get(key_idx, 0) + 1
                wait = 2 ** attempt
                agent_log(
                    "COUNCIL",
                    f"Erro [{model}] key={key_idx} attempt={attempt+1}: {e} — aguardando {wait}s",
                    logging.WARNING,
                )
                await asyncio.sleep(wait)

        return {
            "content":    {},
            "raw":        "",
            "model":      model,
            "key_idx":    -1,
            "tokens":     0,
            "latency_ms": 0,
            "error":      last_error,
        }

    async def council_debate(
        self,
        symbol: str,
        market_context: Dict,
        strategy_proposal: str,
    ) -> Dict:
        """
        Full 3-model debate:
        Model A → analyzes and scores
        Model B → challenges and counter-argues
        Model C → synthesizes final decision
        """
        context_str = json.dumps(market_context, indent=2)

        SYSTEM_BASE = (
            "Você é um agente de trading quantitativo especializado em índices sintéticos da Deriv. "
            "Responda SEMPRE em JSON válido conforme solicitado. Seja preciso e objetivo."
        )

        # ── Model A: Analysis ──────────────────────────────────────────────
        prompt_a = f"""
Símbolo: {symbol}
Contexto de mercado:
{context_str}

Proposta de estratégia: {strategy_proposal}

Analise profundamente e retorne JSON:
{{
  "signal": "CALL" | "PUT" | "HOLD",
  "confidence": 0.0-1.0,
  "score": 0-100,
  "reasoning": "análise detalhada",
  "key_indicators": {{"rsi": 0, "trend": "", "pattern": ""}},
  "risk_level": "LOW" | "MEDIUM" | "HIGH"
}}
"""
        result_a = await self.call(GROQ_MODEL_A, SYSTEM_BASE, prompt_a)

        # ── Model B: Challenge ─────────────────────────────────────────────
        prompt_b = f"""
Símbolo: {symbol}
Contexto de mercado:
{context_str}

Análise do Agente A: {json.dumps(result_a['content'])}

Desafie criticamente esta análise e retorne JSON:
{{
  "agrees": true | false,
  "signal": "CALL" | "PUT" | "HOLD",
  "confidence": 0.0-1.0,
  "counter_points": ["ponto1", "ponto2"],
  "risk_warnings": ["aviso1"],
  "adjusted_score": 0-100
}}
"""
        result_b = await self.call(GROQ_MODEL_B, SYSTEM_BASE, prompt_b)

        # ── Model C: Synthesis ─────────────────────────────────────────────
        prompt_c = f"""
Símbolo: {symbol}
Contexto: {context_str}

Agente A: {json.dumps(result_a['content'])}
Agente B: {json.dumps(result_b['content'])}

Sintetize o debate e tome a decisão final. Retorne JSON:
{{
  "final_signal": "CALL" | "PUT" | "HOLD",
  "final_confidence": 0.0-1.0,
  "consensus": true | false,
  "strategy_name": "nome curto da estratégia",
  "strategy_rules": {{"entry": "", "exit": "", "filters": []}},
  "execution_approved": true | false,
  "veto_reason": "" | "motivo se vetado"
}}
"""
        result_c = await self.call(GROQ_MODEL_C, SYSTEM_BASE, prompt_c)

        total_tokens  = result_a["tokens"] + result_b["tokens"] + result_c["tokens"]
        total_latency = result_a["latency_ms"] + result_b["latency_ms"] + result_c["latency_ms"]

        final = result_c["content"]
        return {
            "symbol":       symbol,
            "model_a":      result_a["content"],
            "model_b":      result_b["content"],
            "model_c":      final,
            "final_signal": final.get("final_signal", "HOLD"),
            "confidence":   final.get("final_confidence", 0.0),
            "approved":     final.get("execution_approved", False),
            "veto_reason":  final.get("veto_reason", ""),
            "strategy":     final.get("strategy_name", ""),
            "tokens_used":  total_tokens,
            "latency_ms":   total_latency,
        }


# ── Singleton ──────────────────────────────────────────────────────────────
GROQ = GroqClient()
