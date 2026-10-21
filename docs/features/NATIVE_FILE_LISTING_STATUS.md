# Native File Listing Status

## S98.4 workflow finding (2026-10-21 UTC)

A normal measurement-consumer commit stopped when pre-commit's git write-tree
reported an existing index.lock. The three tested paths remained staged.
Native ps listed only the two known Python watchdogs; that does not prove Git
process idleness or identify the lock owner. No lock was removed or signalled.
The existing list-files inspection later printed an explicit missing-file
diagnostic for .git/index.lock. A normal guarded retry then passed fresh
128035/128059 collection,24deselected/0errors50.47s/native56.0s and committed
9cae9a0. The original lock's cause remains unproven.

That inspection also exposed a reporting defect: list-files returned0 after find
reported a missing path, because sort was the final pipeline command. Native
output showed absence, but the helper's exit status did not represent producer
success. Future agents need one trustworthy native owner, not a second index
observer, automatic unlock or a hidden status interpretation.

## Preserve the producer's actual outcome

The same list-files target retains the original relative DIR boundary, sorted
output and exact .git/.venv/.mypy_cache/.pytest_cache/.gate-logs pruning.
It writes the native find output to one private namespaced mktemp file, sorts
only after find succeeds, returns find's actual failure status otherwise, and
cleans that temporary file on exit. The source repository is not changed.
No parser, directory walker, cache, daemon, lock manager or dependency is added.

A six-case native fixture owner retains2FAIL/4PASS0.67s/native1.5s before repair;
 /tmp/gludd-v012-list-files-red.log/XML. The failures prove missing producer error
propagation and absent catalog registration. Positive controls keep sorted paths,
spaces, generated-directory pruning and the three original invalid-DIR cases.
The literal fixture executes the actual Make recipe, not a reimplementation.

The target now has an explicit DIR-only contract and safe read-only example:

~~~console
make list-files DIR=.agents/policy
~~~

That exact example lists both existing policy documents. Initial contract
validation correctly refuses the missing help row. Adding the existing target
to make help then passes271 contracts;1161targets/0duplicates and9CI/12local
parity remain. Seven complete listing/contract/Make/help/fixture-guard owners
pass139 on31127.69s/native28.7s and31229.62s/native30.8s, fatal warnings/zero
skips/errors; /tmp/gludd-v012-list-files-whole-311.log/XML and312 equivalent.
This is source/helper acceptance, not runtime admission, gate or release proof.

The existing early admission additionally selects just the missing-input
function as native-directory-listing-producer-status, alongside the three
measurement controls. All prior132 family identities/selectors remain; the
literal roster is136. See the [routing record](INTEGRATION_ADMISSION.md#admit-actual-measurement-and-producer-failures-2026-10-21-utc)
for preserved90/60 limits and separate actual-execution obligations.

## Existing tools and firsthand research

The [GNU Bash pipeline contract](https://www.gnu.org/software/bash/manual/html_node/Pipelines)
defines the default final-command status. A
[firsthand2019 directory-listing discussion](https://www.reddit.com/r/bash/comments/brtawe/if_the_first_command_in_pipe_fails_and_second_one/)
reports an earlier failing listing hidden by a successful downstream formatter.
The forum is historical context; the manual and actual native tests support this
repair. The extensionless primary search result supplied the documented rule;
a later .html open timed out and is not additional evidence.

Use the existing native commands and their real outcomes. This repair does not
authorize git-lock-clean's old raw deletion recipe, automatic retries from elapsed
time, process termination or any broader filesystem access. Full release proof
and the unchanged capacity prerequisite remain pending.
