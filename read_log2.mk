include Makefile
read-gate-summary:
	@cat .gate-logs/gate-20260629-215758.log | grep -E "(FAILED|ERROR|passed|failed|error)" | tail -50
