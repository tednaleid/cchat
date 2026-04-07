"""Argparse entry point for the cchat CLI tool."""

import argparse
import sys

from cchat import formatters
from cchat.commands import agents_cmd, files_cmd, line_cmd, lines_cmd, list_cmd, search_cmd, serve_cmd, spending_cmd, tokens_cmd, view_cmd


class _FullHelpParser(argparse.ArgumentParser):
    """ArgumentParser that prints full --help on errors instead of short usage."""

    def error(self, message):
        sys.stderr.write(f"error: {message}\n\n")
        # If a subcommand was given, show its help instead of top-level
        if self._subparsers is not None:
            for action in self._subparsers._actions:
                if isinstance(action, argparse._SubParsersAction):
                    for arg in sys.argv[1:]:
                        if arg in action.choices:
                            action.choices[arg].print_help(sys.stderr)  # type: ignore[union-attr]
                            sys.exit(2)
        self.print_help(sys.stderr)
        sys.exit(2)


class _SubcommandHelpParser(argparse.ArgumentParser):
    """Subcommand parser that prints its own full --help on errors."""

    def error(self, message):
        sys.stderr.write(f"error: {message}\n\n")
        self.print_help(sys.stderr)
        sys.exit(2)


def main():
    parser = _FullHelpParser(description="Claude Code Chat Browser")
    parser.add_argument("--no-color", action="store_true", help="Disable colored output")

    subparsers = parser.add_subparsers(dest="command", parser_class=_SubcommandHelpParser)
    subparsers.required = True

    list_cmd.register(subparsers)
    view_cmd.register(subparsers)
    line_cmd.register(subparsers)
    lines_cmd.register(subparsers)
    files_cmd.register(subparsers)
    search_cmd.register(subparsers)
    tokens_cmd.register(subparsers)
    spending_cmd.register(subparsers)
    agents_cmd.register(subparsers)
    serve_cmd.register(subparsers)

    args = parser.parse_args()

    if args.no_color:
        formatters.set_no_color(True)

    try:
        args.func(args)
    except KeyboardInterrupt:
        sys.exit(0)
    except BrokenPipeError:
        sys.exit(0)


if __name__ == "__main__":
    main()
