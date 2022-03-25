# argocd-gs-platform-ch-development-apps

## The images tags

In helm-common we use the `sha` if it exists, otherwise the `tag`.

In this repository we have an additional script that will automatically update the sha and introduce tow new tags:

- `lock`: if `true` the `sha` will not be updated anymore.
- `atleastOldDays`: will set the number of days that we will wait before getting it.

This tool will run periodically, and can be triggered by other CI.
