# Engineering Guardrails: Do Not Repeat Troubleshooting Spirals

## Why this note exists

During the Server1 Debian Trixie filming work, a common PXE installation path
expanded into repeated speculative changes and re-analysis. A run was labeled
`proven` without sufficiently durable proof that a fresh installation had
rebooted from its newly written disk and passed first-boot validation. Later
failures triggered discussion of alternate installers, firmware theories,
networking changes, and larger redesigns before the exact failing boundary was
captured.

The result was regression risk, operator frustration, lost filming time, and
unnecessary token and iteration cost. This is a bad BKC engineering pattern.
Do not repeat it.

## Required troubleshooting order

For a previously working pipeline:

1. Freeze the last known-good source and record its commit and artifact hashes.
2. Compare the failed run against that exact source, rendered artifacts,
   inputs, inventory, firmware state, and target hardware state.
3. Identify the last positively observed boundary and the first missing one.
4. Add read-only evidence at that boundary before changing implementation.
5. Change one causal variable only.
6. Run the smallest relevant automated test.
7. Perform one real end-to-end attempt.
8. Keep the change only when the intended end state is independently proven.

Do not begin with architecture changes, alternate products, blog-derived
recipes, or parallel installation paths when a known workflow has regressed.

## Evidence beats narrative

Do not infer successful installation from any of the following alone:

- PXE DHCP success
- kernel or initrd download
- preseed download
- installer process visibility
- package installation output
- a GRUB command exiting successfully
- an address answering SSH without proving target identity and deployment ID

A bare-metal Debian run is successful only when the same deployment proves:

- expected disk identity and firmware mode before erasure
- expected partition table and filesystems after installation
- GRUB target appropriate to the active firmware mode
- installed kernel, initramfs, and generated `grub.cfg`
- PXE disarmed before reboot
- local-disk boot
- expected host identity over SSH
- matching BKC deployment marker
- first-boot filesystem, network, SSH, and inventory validation

Until then, the pipeline remains `candidate`, even if it worked once.

## One-path rule

Maintain one Debian Trixie bare-metal workflow with an explicit firmware branch
inside it. Do not accumulate competing direct-login fixes, rescue scripts,
standalone wipe sessions, and alternate pipeline copies.

Normal debugging may use BKC SSH, iDRAC SOL, Redfish, HTTP logs, or rescue media
to collect evidence. Any required repair must be folded back into the single
pipeline before declaring success.

## Change budget

After the first failed retry:

- stop automatic retries;
- do not change more than one behavioral variable without a written causal
  explanation;
- do not widen scope until the current boundary has evidence;
- do not call a workaround a fix;
- do not promote a pipeline based on manually repaired state.

If two consecutive attempts fail at the same boundary, require a focused
diagnostic or controlled reference installation before another destructive
attempt.

## Known-good fragments

Once a script or template is end-to-end proven:

- assign it a stable fragment ID;
- record its SHA-256 digest;
- add a focused contract test;
- document the proof run;
- avoid editing it while debugging orchestration around it;
- update its digest only after a new end-to-end proof.

“Known good” means the resulting machine or service reached its validated end
state. It does not mean the fragment merely executed.

## Communication discipline

Lead with observed facts, the exact failing boundary, and the single next test.
Do not repeatedly reassure the operator that a pipeline is ready when final
validation is missing. During filming, use unambiguous cues:

- `HOLD` — candidate or diagnostic work remains.
- `RECORD NOW` — the exact deployed version has completed its proof run.

## Current Server1 lesson

For Server1, firmware, PERC, DHCP, iPXE, kernel/initrd delivery, and preseed
delivery were all observed. The unresolved boundary was Debian disk/GRUB result
through local-disk first boot. Work outside that boundary was noise until that
evidence existed.

The correction must remain narrow, be tested as a candidate, and must not be
labeled proven until Server1 boots from disk and passes the complete first-boot
receipt twice without direct repair.
