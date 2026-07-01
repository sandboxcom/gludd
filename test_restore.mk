# Makefile restored from git
include Makefile

dummy:
	@cat Makefile | sed -n '280,290p'

restore-makefile:
	git show HEAD:Makefile > Makefile.new && mv Makefile.new Makefile
