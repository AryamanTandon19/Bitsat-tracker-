"""Find the DVR and connect to it, with nobody typing anything.

The requirement is a system that connects to a real CCTV installation without
a human touching it. That means the box has to work out three things by
itself: which network it is on, which device on it is a recorder, and what
credentials open it.

  **Which network.** Read it off the machine's own interfaces. A box plugged
  into the society's switch is already on the right subnet, so its own address
  gives us the range without anyone typing a CIDR.

  **Which device.** Anything answering on the RTSP port.

  **Which credentials.** Recorders ship with a small, well-known set, and most
  installers never change them. That is a security problem for the society and
  a convenience for us, and it is why the list is short and ordered by how
  common it is rather than exhaustive: this is auto-configuration of the
  owner's own recorder, not a password attack. `max_attempts_per_host` caps it
  so a