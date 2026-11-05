# Skill: machines-from-labels

Use when: a store's series carry a service, job or app label and the owner asks which machines they measure, or you would attach series to a declared machine.

- A machine is known by its events on the bus, or by what the owner declares: never by who wrote a
  series. A label never makes a machine, and a flow is never associated with a machine from a
  measurement's label (only by its events, as an inferred hypothesis).
- A store's `machine` label (`[[sources]]`, the `store-setup` skill) is the one that names the
  service doing the work: `service`, `job`, `app`; never a pod or an instance. `[sources.labels]`
  joins spellings of one label.
- Series measure a declared machine only by an attachment the owner confirms: `propose_attachment`
  with the instrument, the match (a series-name `prefix` or `labels`, read from `list_metrics`
  first, given explicitly) and `instance_label` when one label's values are its replicas; change it
  with `revise_attachment`. It is declared provenance: it never makes the machine seen or active,
  and its replicas are counts of that label's values, never instances heard on the bus.
- A machine the owner has not named is proposed in one line and waits for their words; never
  declare one from a label alone. "Measured, never heard" on the Machines page is normal for an
  external machine known by its measurements (the `page-states` skill).
