# Config Profiles

Optional profile files can be placed here for specific experiments, for example:

```text
spectrum_1890.json
raw_iq_2x2_1890.json
csi_offline_default.json
```

The current scripts load `config/default_config.json` plus optional
`config/devices.local.json`. Profile selection is reserved for a future CLI
extension.
