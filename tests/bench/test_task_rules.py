"""Each rule a task's files can show has a made-up task that breaks only that rule."""

import pytest

from lamplighter.bench import PINNED_IMAGE, REQUIRED, TaskFile, task_faults

NAME = "made-up-task"
TASK_TOML = """\
schema_version = "1.4"
# The check runs in a second container, on a copy of this folder.
artifacts = ["/app"]

[task]
name = "lamplighter-bench/made-up-task"
description = "Change one value in a made-up settings file."

[metadata]
group = "files"
drawn_from = "a made-up rule"

[agent]
timeout_sec = 1500.0

[verifier]
timeout_sec = 300.0
environment_mode = "separate"

[environment]
build_timeout_sec = 600.0
cpus = 1
memory_mb = 1024
"""
DOCKERFILE = f"""\
FROM {PINNED_IMAGE}
WORKDIR /app
COPY files/ /app/
COPY --chmod=644 outside/LICENSE /opt/vendor/LICENSE
RUN /app/prepare.sh
"""
TEST_SH = """\
#!/bin/bash
if cmp -s /app/settings.ini /tests/expected.ini; then
  echo 1 > /logs/verifier/reward.txt
else
  echo 0 > /logs/verifier/reward.txt
fi
"""
GOOD = {
    "instruction.md": "Set port to 8080 in settings.ini. The docs are at https://docs.example.invalid/.\n",
    "task.toml": TASK_TOML,
    "environment/Dockerfile": DOCKERFILE,
    "environment/.dockerignore": ".DS_Store\n",
    "environment/files/settings.ini": "port = 80\n",
    "environment/files/prepare.sh": "#!/bin/bash\nmkdir -p /app/logs\n",
    # A task's own files may name an installer: a script that fails on purpose.
    "environment/files/download.sh": "#!/bin/bash\ncurl http://prices.example.invalid/list.csv\n",
    "environment/outside/LICENSE": "A made-up licence.\n",
    "solution/solve.sh": "#!/bin/bash\nsed -i 's/80/8080/' /app/settings.ini\n",
    "tests/test.sh": TEST_SH,
    "tests/expected.ini": "port = 8080\n",
}


def _files(texts: dict[str, str]) -> list[TaskFile]:
    return [
        TaskFile(path, text.encode(), executable=path.endswith(".sh"), link=False)
        for path, text in texts.items()
    ]


def _faults(name: str = NAME, **changed: str | None) -> tuple[str, ...]:
    texts = dict(GOOD)
    for key, text in changed.items():
        path = key.replace("__", "/").replace("_dot_", ".")
        if text is None:
            del texts[path]
        else:
            texts[path] = text
    return task_faults(name, _files(texts))


def test_a_task_that_keeps_every_rule_has_no_fault() -> None:
    assert _faults() == ()


@pytest.mark.parametrize("name", ["Made-Up", "made_up", "made-up-", "-made", ""])
def test_a_folder_name_that_is_not_lowercase_words_with_hyphens(name: str) -> None:
    faults = task_faults(name, _files(GOOD))
    assert len(faults) == 1
    assert "name" in faults[0]


@pytest.mark.parametrize("path", REQUIRED)
def test_a_task_without_one_of_its_files(path: str) -> None:
    texts = {p: t for p, t in GOOD.items() if p != path}
    assert task_faults(NAME, _files(texts)) == (f"There is no {path}.",)


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ('name = "lamplighter-bench/made-up-task"', 'name = "lamplighter-bench/other"'),
        ('name = "lamplighter-bench/made-up-task"', 'name = "made-up-task"'),
        ('artifacts = ["/app"]', "artifacts = []"),
        ('artifacts = ["/app"]\n', ""),
        ('environment_mode = "separate"', 'environment_mode = "shared"'),
        ("timeout_sec = 1500.0", "timeout_sec = 1200.0"),
        ("memory_mb = 1024", 'memory_mb = 1024\nnetwork_mode = "bridge"'),
        ('schema_version = "1.4"', 'schema_version = "1.3"'),
        ("[environment]", "[extra]\nkey = 1\n\n[environment]"),
    ],
)
def test_a_task_toml_that_differs_from_the_fixed_form(old: str, new: str) -> None:
    faults = _faults(task_dot_toml=TASK_TOML.replace(old, new, 1))
    assert len(faults) == 1
    assert faults[0].startswith("task.toml differs")


@pytest.mark.parametrize(
    ("old", "new", "word"),
    [
        ('group = "files"', 'group = "misc"', "group"),
        (
            'description = "Change one value in a made-up settings file."',
            'description = ""',
            "description",
        ),
        ('drawn_from = "a made-up rule"\n', "", "drawn_from"),
        ("[task]", "[task", "not TOML"),
    ],
)
def test_a_task_toml_with_a_free_line_wrong(old: str, new: str, word: str) -> None:
    faults = _faults(task_dot_toml=TASK_TOML.replace(old, new, 1))
    assert len(faults) == 1
    assert word in faults[0]


@pytest.mark.parametrize(
    ("old", "new"),
    [
        (f"FROM {PINNED_IMAGE}", "FROM python:3.13-slim-bookworm"),
        (f"FROM {PINNED_IMAGE}", "FROM debian:bookworm-slim"),
        ("WORKDIR /app", "WORKDIR /srv"),
        ("RUN /app/prepare.sh", "RUN /app/other.sh"),
        ("RUN /app/prepare.sh", "RUN /opt/vendor/prepare.sh"),
        ("RUN /app/prepare.sh", "RUN prepare.sh"),
        ("RUN /app/prepare.sh", "RUN /app/prepare.sh --fast"),
        ("RUN /app/prepare.sh", "RUN chmod +x /app/prepare.sh"),
        ("COPY --chmod=644 outside/", "ADD --chmod=644 outside/"),
        ("COPY --chmod=644 outside/", "COPY --chmod=644 --chown=1000 outside/"),
        ("COPY --chmod=644 outside/LICENSE", "COPY --chmod=644 tests/expected.ini"),
        ("COPY --chmod=644 outside/LICENSE", "COPY --chmod=644"),
        ("COPY --chmod=644 outside/", "COPY outside/"),
        ("WORKDIR /app", "WORKDIR /app\nENV MADE_UP=1"),
    ],
)
def test_a_dockerfile_line_it_may_not_have(old: str, new: str) -> None:
    faults = _faults(environment__Dockerfile=DOCKERFILE.replace(old, new, 1))
    assert len(faults) == 1, faults
    assert "Dockerfile" in faults[0] or "COPY" in faults[0]


def test_a_run_of_a_single_file_copied_in_is_allowed() -> None:
    one = DOCKERFILE.replace("COPY files/ /app/", "COPY files/prepare.sh /app/")
    assert _faults(environment__Dockerfile=one) == ()
    named = one.replace("files/prepare.sh /app/", "files/prepare.sh /app/prepare.sh")
    assert _faults(environment__Dockerfile=named) == ()


def test_a_comment_and_a_blank_line_in_the_dockerfile_are_allowed() -> None:
    commented = DOCKERFILE.replace(
        "WORKDIR /app", "# Where the agent works.\n\nWORKDIR /app"
    )
    assert _faults(environment__Dockerfile=commented) == ()


@pytest.mark.parametrize("named", ["/solution/solve.sh", "/app/tests/expected.ini"])
def test_a_check_that_reads_what_the_agent_can_reach(named: str) -> None:
    faults = _faults(tests__test_dot_sh=TEST_SH.replace("/tests/expected.ini", named))
    assert len(faults) == 1
    assert "can reach" in faults[0]


def test_a_check_that_reads_a_copy_it_does_not_have() -> None:
    faults = _faults(tests__test_dot_sh=TEST_SH.replace("expected.ini", "gone.ini"))
    assert faults == ("tests/test.sh reads /tests/gone.ini, which is not in tests/.",)


@pytest.mark.parametrize("word", ["pip", "apt", "apt-get", "curl", "wget", "uv"])
def test_a_check_that_installs_something(word: str) -> None:
    faults = _faults(tests__test_dot_sh=f"{word} install made-up\n{TEST_SH}")
    assert faults == (f"tests/test.sh installs something: it names {word}.",)


def test_a_shell_file_of_the_check_that_installs_something() -> None:
    faults = _faults(tests__setup_dot_sh="#!/bin/bash\nwget made-up\n")
    assert faults == ("tests/setup.sh installs something: it names wget.",)


def test_a_dockerfile_that_installs_something() -> None:
    faults = _faults(environment__Dockerfile=f"{DOCKERFILE}# pip is not here\n")
    assert faults == ("environment/Dockerfile installs something: it names pip.",)


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/list",
        "http://prices.example.net",
        "https://invalid.example/",
    ],
)
def test_a_host_that_is_not_under_invalid(url: str) -> None:
    faults = _faults(environment__files__notes_dot_txt=f"See {url} for more.\n")
    assert len(faults) == 1
    assert "not under .invalid" in faults[0]


def test_an_instruction_that_names_app() -> None:
    faults = _faults(instruction_dot_md="Set port to 8080 in /app/settings.ini.\n")
    assert faults == (
        "instruction.md names /app: it names paths inside the work folder.",
    )


def test_a_dockerignore_that_is_not_the_one_line() -> None:
    faults = _faults(environment___dot_dockerignore=".DS_Store\n*.pyc\n")
    assert faults == ("environment/.dockerignore is not the one line .DS_Store.",)
