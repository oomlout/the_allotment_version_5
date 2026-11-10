# Oomlout roboclick

`framework.py` is copied unchanged from `C:/gh/oomlout_roboclick/oomlout_roboclick.py`, commit `028f43b9e8393e19a669bc4c66f3ee7305331a93`.
`desktop.py` and `actions/roboclick_action_desktop_*/working.py` are new reusable desktop actions, also added to that checkout.

The Allotment uses the framework action discovery and run_action dispatcher with this isolated actions directory. Legacy AI actions and the legacy mouse-failsafe recovery wrapper are not loaded. No runtime AI service, browser debug protocol, extension, or driver is used.

The desktop extension now uses Win+Left then Win+Up to snap Chrome into the top-left quarter. The upstream desktop copy is synchronized. `desktop_ready` is an Allotment-specific manual-review action; it only locates the final control and saves evidence, never clicks it.
