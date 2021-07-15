.PHONY: update
update:
	for f in $$(ls -1 charts/); do echo --- charts/$$f ---; helm3 dependency update charts/$$f; done
	for f in $$(ls -1 apps/); do echo --- apps/$$f ---; helm3 dependency update apps/$$f; done
