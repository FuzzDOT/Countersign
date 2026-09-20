"""No generated text in the explanation or voice paths. Brief §8, §11.

The project's loudest claim is that nothing a user reads was written by a
language model. Ablation interpretations, briefing scripts and spoken
answers are all template-filled over structured fields plus the cited
sentence — and the only way that claim survives hour 23, when someone is
tired and an LLM call would be the quickest fix, is a test that reads the
source.

So this walks the AST of every module on those paths and fails on any
import or call that could reach a text-generating upstream.

**ElevenLabs is allowed in the voice path and only there.** It converts text
we wrote into audio; it does not write text. That distinction is the whole
policy, so it is encoded as an allowlist with the reason attached rather
than left to whoever reads the diff.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parent.parent

# Everything a user reads comes from here.
NO_GENERATION_PATHS = (
    "ml/ablation",
    "ml/voice",
    "api/v1/ablation.py",
    "api/v1/voice.py",
    "ml/cascade/routing.py",
)

# Modules that can produce text from a prompt. `ml.cascade.nemotron` is ours
# and is fine in the cascade; it is forbidden *here*.
GENERATIVE_MODULES = frozenset(
    {
        "ml.cascade.nemotron",
        "ml.cascade.prompt",
        "ml.cascade.cascade",
        "openai",
        "anthropic",
        "cohere",
        "transformers",
        "langchain",
        "litellm",
    }
)

# Raw HTTP is banned on these paths too: a generative call made with httpx
# directly would sail past a module allowlist.
NETWORK_MODULES = frozenset({"httpx", "requests", "aiohttp", "urllib.request", "http.client"})

# Text in, audio out. Not a generator, and allowed only where it belongs.
AUDIO_ONLY_MODULES = frozenset({"elevenlabs"})
AUDIO_ALLOWED_UNDER = ("ml/voice",)

# The raw-HTTP ban exists to stop a generative call from sailing past the
# module allowlist above by going straight to an HTTP client instead of an
# SDK — which is exactly what `ml/voice/tts.py` and `ml/voice/stt.py` do,
# deliberately, to reach ElevenLabs (the same reasoning `ml/cascade/nemotron.py`
# already established for the real generative call this project makes: a
# pinned `httpx` version drifts less than a hosted-model SDK's method names
# do, and this build has been bitten by exactly that twice this session).
#
# The carve-out is these two *files*, not the whole `ml/voice` directory —
# unlike the SDK-import allowlist above, where any file under `ml/voice`
# importing `elevenlabs` is fine, a raw HTTP call sneaking into
# `ml/voice/answer.py` or `ml/voice/intent.py` would be exactly the
# injection risk this test guards against: it could reach anywhere, not
# only ElevenLabs. Narrower than the module-level allowlist, on purpose.
NETWORK_ALLOWED_FILES = frozenset({"ml/voice/tts.py", "ml/voice/stt.py"})


def _modules_under(path: str) -> list[Path]:
    target = BACKEND_ROOT / path
    if target.is_file():
        return [target]
    if target.is_dir():
        return sorted(target.rglob("*.py"))
    return []


def _imported_names(tree: ast.AST) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
            names.update(f"{node.module}.{alias.name}" for alias in node.names)
    return names


def _root(name: str) -> str:
    return name.split(".")[0]


@pytest.mark.parametrize("path", NO_GENERATION_PATHS)
def test_no_generative_import_on_the_explanation_paths(path: str) -> None:
    offenders: list[str] = []
    for module in _modules_under(path):
        for name in _imported_names(ast.parse(module.read_text(encoding="utf-8"))):
            if name in GENERATIVE_MODULES or _root(name) in GENERATIVE_MODULES:
                offenders.append(f"{module.relative_to(BACKEND_ROOT)} imports {name}")
    assert not offenders, (
        "text a user reads must be template-filled, never generated "
        "(brief §8, §11):\n  " + "\n  ".join(offenders)
    )


@pytest.mark.parametrize("path", NO_GENERATION_PATHS)
def test_no_raw_http_on_the_explanation_paths(path: str) -> None:
    """A generative call made with httpx directly would sail past a module
    allowlist, so the transport is banned as well as the client — except
    the two files that *are* the audited ElevenLabs transport, see
    `NETWORK_ALLOWED_FILES`."""
    offenders: list[str] = []
    for module in _modules_under(path):
        if module.relative_to(BACKEND_ROOT).as_posix() in NETWORK_ALLOWED_FILES:
            continue
        for name in _imported_names(ast.parse(module.read_text(encoding="utf-8"))):
            if name in NETWORK_MODULES or _root(name) in NETWORK_MODULES:
                offenders.append(f"{module.relative_to(BACKEND_ROOT)} imports {name}")
    assert not offenders, "no outbound HTTP on these paths:\n  " + "\n  ".join(offenders)


@pytest.mark.parametrize("path", NO_GENERATION_PATHS)
def test_audio_synthesis_only_appears_in_the_voice_path(path: str) -> None:
    """ElevenLabs turns our text into audio. It is not a generator, and it
    has no business anywhere but the voice modules."""
    if path.startswith(AUDIO_ALLOWED_UNDER):
        return
    offenders: list[str] = []
    for module in _modules_under(path):
        for name in _imported_names(ast.parse(module.read_text(encoding="utf-8"))):
            if _root(name) in AUDIO_ONLY_MODULES:
                offenders.append(f"{module.relative_to(BACKEND_ROOT)} imports {name}")
    assert not offenders, "\n  ".join(offenders)


def test_the_network_carve_out_is_exactly_these_two_files() -> None:
    """`NETWORK_ALLOWED_FILES` is meant to be as narrow as possible — this
    fails the day someone adds raw HTTP to a third file under `ml/voice`
    and "fixes" the resulting failure by widening the allowlist instead of
    asking why a raw socket showed up in, say, `answer.py`."""
    actually_importing_http: set[str] = set()
    for module in _modules_under("ml/voice"):
        for name in _imported_names(ast.parse(module.read_text(encoding="utf-8"))):
            if name in NETWORK_MODULES or _root(name) in NETWORK_MODULES:
                actually_importing_http.add(module.relative_to(BACKEND_ROOT).as_posix())
    assert actually_importing_http == NETWORK_ALLOWED_FILES


def test_the_guard_covers_paths_that_exist() -> None:
    """A renamed package would make every assertion above vacuous.

    No exemption anymore: `ml/voice` was allowed to be absent before Stage 8
    landed (a stub-stage tolerance, `docs/STATE.md`) — now that it exists,
    the tripwire should catch it disappearing again just as hard as it
    catches `ml/ablation` disappearing.
    """
    found = {path for path in NO_GENERATION_PATHS if _modules_under(path)}
    missing = set(NO_GENERATION_PATHS) - found
    assert not missing, f"these paths vanished: {sorted(missing)}"
    assert len(found) == len(NO_GENERATION_PATHS)


def test_the_guard_would_catch_a_violation(tmp_path: Path) -> None:
    """A guard nobody has seen fail is a guard nobody knows works."""
    offender = tmp_path / "sneaky.py"
    offender.write_text("from ml.cascade.nemotron import NemotronClient\n")
    names = _imported_names(ast.parse(offender.read_text()))
    assert any(name in GENERATIVE_MODULES for name in names)

    innocent = tmp_path / "fine.py"
    innocent.write_text("from ml.ablation import templates\n")
    assert not any(
        name in GENERATIVE_MODULES or _root(name) in GENERATIVE_MODULES
        for name in _imported_names(ast.parse(innocent.read_text()))
    )


def test_ablation_interpretations_come_from_the_template_module() -> None:
    """The engine must not assemble prose of its own — every user-visible
    sentence has to be one somebody wrote down in `templates.py`."""
    source = (BACKEND_ROOT / "ml/ablation/engine.py").read_text(encoding="utf-8")
    tree = ast.parse(source)

    assigned: list[ast.AST] = [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "interpretation"
            for target in node.targets
        )
    ]
    assert assigned, "engine.py no longer assigns an interpretation"
    for value in assigned:
        rendered = ast.unparse(value)
        assert "templates.render" in rendered, rendered
