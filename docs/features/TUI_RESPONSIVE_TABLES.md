# Responsive TUI Tables

Gludd's TUI table builders treat the measured panel width as an explicit render
contract. Every builder passes its `term_width` to Rich's `Table`, and the safe
default is 60 columns for callers that do not yet provide a measurement. The
interactive runner supplies the live left-, right-, or terminal-panel width, so
normal and wide terminals still use their available space.

## Operator evidence

This behavior addresses a long-lived Rich operator report from January 2020:
[Textualize/rich issue #2](https://github.com/Textualize/rich/issues/2). The
reporter observed output being constrained to an unexpected 80 columns. Rich's
maintainer explained that console dimensions, `expand=True`, explicit widths,
and column constraints all participate in sizing. That history matches Gludd's
failure mode: builders computed adaptive columns but left the table itself at
Rich's 80-column capture default.

Rich's own CLI exposes an explicit output width for the same reason; see the
[rich-cli width documentation](https://github.com/Textualize/rich-cli#width).
The practical invariant is that the producer's measured width must reach the
renderable instead of relying on an unrelated console default.

## Acceptance contract

- Every `_build_*_table` function accepts `term_width`.
- The runner supplies the current panel width during every refresh.
- Tables render at 40, 60, 80, 120, 160, and 200 columns without overflow.
- A builder called without a width remains safe in a 60-column console.
- Column text may truncate with an ellipsis; borders and adjacent panels may
  never overflow.

The executable coverage is in
`tests/unit/test_tui_screen_real_estate.py` and the surrounding TUI layout and
builder suites.
