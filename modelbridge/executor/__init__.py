"""ModelBridge executor — Phase 7 command runner & error parser.

The executor lets ``mbridge run`` (and the upcoming ``fix`` / ``loop``)
invoke shell commands inside a project directory while keeping a strict
allow/deny policy and parsing common error shapes back into structured
records.

Phase-7 subset A exposes three pieces:

* :class:`CommandResult` + :func:`run_command` — the subprocess runner.
* :class:`CommandPolicy` + :class:`CommandRejected` — the validator.
* :class:`ParsedError` + :func:`parse_output` — the failure analyser.
"""

from .command_validator import CommandPolicy, CommandRejected
from .output_parser import ErrorType, ParsedError, parse_output
from .runner import CommandResult, run_command


__all__ = [
    "CommandPolicy",
    "CommandRejected",
    "CommandResult",
    "ErrorType",
    "ParsedError",
    "parse_output",
    "run_command",
]
