flowchart LR
  intent["Recipe intent\nfoo.bar"] --> identity["Identity + homes\nLDAP / SMB"]
  identity --> crm["CRM / intranet\nHTTP health"]
  identity --> linux["Linux dev workstations\nPXE + MATE + Git"]
  identity --> windows["Windows helpdesk\nPXE + browser + RustDesk"]
  crm --> validate["Layered validation"]
  linux --> validate
  windows --> validate
  validate --> managed["Managed small office"]

  classDef pending fill:#1f2937,stroke:#64748b,color:#f8fafc
  classDef running fill:#1e3a8a,stroke:#3b82f6,color:#eff6ff
  classDef success fill:#064e3b,stroke:#22c55e,color:#ecfdf5
  class intent,identity,crm,linux,windows pending
  class validate running
  class managed success
