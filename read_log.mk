include Makefile
read-gate-log:
	@cat .gate-logs/gate-20260629-215758.log | tail -100
