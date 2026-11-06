"""Qwen prompt expansion through an OpenAI-compatible ``/chat/completions``
endpoint (LM Studio, Ollama, llama.cpp server, vLLM...).

Qwen turns the user's short description into an image prompt for the preview
stage. The archetype rules are enforced here, not trusted to the model:
``archetype_override`` always wins, gear and weapons are always previewed on
the biped, and an archetype that is not a template falls back to biped with a
flag the reviewer sees.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request

from ...config import Settings
from ...defaults import GEAR_SLOTS, WEAPON_CLASSES

ATTEMPTS = 2  # one retry on an invalid reply


class LLMError(RuntimeError):
    """A readable failure of the prompt stage."""


class _FormatRefused(Exception):
    """The server refused the requested response_format; try the next one."""


SYSTEM_PROMPT = """You write prompts for an image model that makes ONE concept image of a game asset.
The image is later turned into a 3D mesh and rigged, so the rules below matter more than style.

Answer with a single JSON object and nothing else:
{{"archetype": "<one of the archetype ids>", "prompt": "<positive prompt>",
  "negative_prompt": "<negative prompt>", "slot": <gear slot or null>, "weapon_class": <weapon class or null>}}

Archetypes (id: what it is. Rest pose to use):
{archetypes}

Rules for every prompt:
- The subject stands alone, whole and uncropped, in the chosen archetype's rest pose, exactly as written above.
- Neutral plain light-grey background, no scenery, no props, no text.
- Flat, even, shadowless studio lighting; no dramatic light, no rim light, no motion blur.
- Full body in frame, centred, facing the camera.
- Describe shapes, materials and colours concretely in one paragraph of comma-separated phrases.
- The negative prompt lists what would break the mesh: extra limbs, cropped parts, dynamic pose,
  busy background, cast shadows, several subjects.
{kind_rules}"""

KIND_RULES = {
    "gear": """This asset is GEAR (an armour or clothing piece for the "{slot}" slot{slot_note}).
- Show the piece alone, worn by a plain featureless grey mannequin in the biped rest pose; nothing else is worn.
- "archetype" is always "biped". "slot" is one of: {slots}. "weapon_class" is null.""",
    "weapon": """This asset is a WEAPON ({weapon_note}).
- Show the weapon alone, side-on, upright and centred, with no hands, no character and no stand.
- "archetype" is always "biped". "weapon_class" is one of: {classes}. "slot" is null.""",
    "creature": """This asset is a {kind}.
- Pick the archetype whose body plan fits the description best{override_note}.
- Empty-handed: no weapons, shields, staffs, wands, torches, bags or any held item, even when the
  description mentions one (weapons and gear are made as separate assets). Hands open and relaxed.
- Seen straight from the front, unless the archetype's rest pose names another view. Limbs apart from
  the body and from each other, so every limb has a clear gap around it.
- Compact, solid silhouette: no loose chains, floating particles, smoke, fire or effects around the body.
- "slot" and "weapon_class" are null.""",
}

# Always added to the negative prompt of heroes, monsters and bosses: held items and loose
# effects become junk geometry in the mesh, and side or three-quarter views reconstruct worse.
CREATURE_NEGATIVE = ("weapon, sword, shield, staff, holding an object, props, particles, floating debris, "
                     "smoke, aura")
# Bipeds are reconstructed best from the front; other rest poses name their own view.
BIPED_NEGATIVE = "three-quarter view, side view"


def build_system_prompt(*, kind: str, archetypes: dict[str, dict], archetype_override: str | None,
                        slot: str | None, weapon_class: str | None, notes: list[str]) -> str:
    lines = "\n".join(f"- {aid}: {spec.get('description', spec.get('name', aid))} Rest pose: {spec['rest_pose']}"
                      for aid, spec in archetypes.items())
    if kind == "gear":
        rules = KIND_RULES["gear"].format(
            slot=slot or "best fitting", slots=", ".join(GEAR_SLOTS),
            slot_note=" chosen by the user" if slot else "; pick the slot from the description")
    elif kind == "weapon":
        rules = KIND_RULES["weapon"].format(
            classes=", ".join(WEAPON_CLASSES),
            weapon_note=f"class {weapon_class}, chosen by the user" if weapon_class
            else "pick the class from the description")
    else:
        override_note = (f'. The user chose the archetype "{archetype_override}": use it and its rest pose'
                         if archetype_override else "")
        rules = KIND_RULES["creature"].format(kind=kind, override_note=override_note)
    text = SYSTEM_PROMPT.format(archetypes=lines, kind_rules=rules)
    kept = [n.strip() for n in notes if n and n.strip()]
    if kept:
        text += ("\n\nThe reviewer rejected earlier images of this asset. Fold every one of these "
                 "notes into the new prompt:\n" + "\n".join(f"- {n}" for n in kept))
    return text


def parse_reply(content: str) -> dict:
    """The JSON object in the model's reply, or ``ValueError``."""
    text = re.sub(r"<think>.*?</think>", "", content or "", flags=re.S).strip()
    fenced = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, flags=re.S)
    if fenced:
        text = fenced.group(1)
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("the reply is not a JSON object")
    if not isinstance(data.get("prompt"), str) or not data["prompt"].strip():
        raise ValueError('the reply has no "prompt" text')
    return data


REPLY_SCHEMA = {
    "type": "object",
    "properties": {
        "archetype": {"type": "string"},
        "prompt": {"type": "string"},
        "negative_prompt": {"type": "string"},
        "slot": {"type": ["string", "null"]},
        "weapon_class": {"type": ["string", "null"]},
    },
    "required": ["archetype", "prompt", "negative_prompt", "slot", "weapon_class"],
    "additionalProperties": False,
}

# JSON modes to ask for, best first. LM Studio accepts only json_schema (it
# rejects json_object with HTTP 400); older servers know only json_object; the
# last resort asks for nothing and relies on the prompt and parse_reply.
RESPONSE_FORMATS: list[dict | None] = [
    {"type": "json_schema", "json_schema": {"name": "asset_prompt", "strict": True, "schema": REPLY_SCHEMA}},
    {"type": "json_object"},
    None,
]


class QwenLLM:
    timeout = 180.0

    def __init__(self, settings: Settings):
        self.url = settings.qwen_url.rstrip("/") + "/chat/completions"
        self.model = settings.qwen_model
        self._format = 0  # index into RESPONSE_FORMATS; moves on when the server refuses one

    def _post(self, messages: list[dict]) -> str:
        while True:
            try:
                return self._post_once(messages, RESPONSE_FORMATS[self._format])
            except _FormatRefused:
                self._format += 1

    def _post_once(self, messages: list[dict], response_format: dict | None) -> str:
        fields = {"model": self.model, "messages": messages, "temperature": 0.4}
        if response_format is not None:
            fields["response_format"] = response_format
        body = json.dumps(fields).encode("utf-8")
        request = urllib.request.Request(self.url, data=body, method="POST",
                                         headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:300]
            if exc.code in (400, 422) and response_format is not None:
                raise _FormatRefused() from exc
            raise LLMError(f"Qwen at {self.url} answered HTTP {exc.code}: {detail}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            reason = getattr(exc, "reason", exc)
            raise LLMError(f"Could not reach Qwen at {self.url} ({reason}). Is the server running and "
                           f"is the model {self.model!r} pulled?") from exc
        except ValueError as exc:
            raise LLMError(f"Qwen at {self.url} did not answer JSON.") from exc
        try:
            return payload["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError(f"Qwen at {self.url} answered without a message.") from exc

    def expand(self, *, description, kind, archetypes, archetype_override, slot, weapon_class, notes):
        system = build_system_prompt(kind=kind, archetypes=archetypes, archetype_override=archetype_override,
                                     slot=slot, weapon_class=weapon_class, notes=notes)
        messages = [{"role": "system", "content": system},
                    {"role": "user", "content": f"Asset kind: {kind}\nDescription: {description.strip()}"}]
        problem = ""
        for attempt in range(ATTEMPTS):
            content = self._post(messages)
            try:
                data = parse_reply(content)
                break
            except ValueError as exc:
                problem = str(exc) if not isinstance(exc, json.JSONDecodeError) else f"invalid JSON ({exc.msg})"
                messages = messages + [
                    {"role": "assistant", "content": content or ""},
                    {"role": "user", "content": f"That reply was not usable: {problem}. Answer again with only "
                                                "the JSON object described in the instructions."}]
        else:
            raise LLMError(f"Qwen did not give a usable prompt after {ATTEMPTS} tries: {problem}.")
        return self._settle(data, kind=kind, archetypes=archetypes, archetype_override=archetype_override,
                            slot=slot, weapon_class=weapon_class)

    @staticmethod
    def _settle(data: dict, *, kind, archetypes, archetype_override, slot, weapon_class) -> dict:
        flags: list[str] = []
        answered = data.get("archetype")
        if kind in ("gear", "weapon"):
            archetype = "biped"  # items are previewed on the biped mannequin
        elif archetype_override:
            archetype = archetype_override
        elif isinstance(answered, str) and answered in archetypes:
            archetype = answered
        else:
            archetype = "biped"
            flags.append(f"Qwen chose the archetype {answered!r}, which is not a template; biped is used instead.")
        if kind == "gear":
            slot = slot or (data.get("slot") if data.get("slot") in GEAR_SLOTS else "chest")
        if kind == "weapon":
            weapon_class = weapon_class or (data.get("weapon_class") if data.get("weapon_class") in WEAPON_CLASSES
                                            else "one_handed")
        negative = data.get("negative_prompt")
        negative = negative.strip() if isinstance(negative, str) else ""
        if kind not in ("gear", "weapon"):
            extra = (CREATURE_NEGATIVE, BIPED_NEGATIVE if archetype == "biped" else "")
            negative = ", ".join(p for p in (negative, *extra) if p)
        return {
            "archetype": archetype,
            "prompt": data["prompt"].strip(),
            "negative_prompt": negative,
            "slot": slot if kind == "gear" else None,
            "weapon_class": weapon_class if kind == "weapon" else None,
            "flags": flags,
        }
