<!--
SPDX-FileCopyrightText: 2026 Gabriele Capparelli
SPDX-License-Identifier: Apache-2.0
-->

# Service Aliases — Curated Map

The auto-generated `aliases` field in `assets/shape-index.json` covers common acronyms (e.g. "Build Process Automation" → "BPA"). This document captures **additional, manually-curated aliases** that are intuitive to practitioners but not derivable from the canonical name.

When `sap-icons-resolve` cannot find a match via the index, it should consult this list as a tier-2 lookup before giving up.

## Common alias map

| Canonical SAP name | Common aliases |
|---|---|
| SAP Build Process Automation | BPA, Build Process Auto, Workflow Mgmt |
| SAP Build Apps | Build Apps, AppGyver |
| SAP Build Code | Build Code, BAS Build, Build Tools |
| SAP Build Work Zone, advanced edition | Work Zone, Launchpad, Fiori Launchpad, FLP |
| SAP Cloud Application Programming Model | CAP, CDS, @sap/cds |
| SAP Cloud Connector | Cloud Connector, SCC |
| SAP Cloud Foundry Runtime | Cloud Foundry, CF, CF Runtime |
| SAP Kyma Runtime | Kyma, K8s Runtime |
| SAP Cloud Integration | CPI, Cloud Integration, Integration Flows |
| SAP Integration Suite | IS, Integration Suite |
| SAP API Management | API Mgmt, APIM |
| SAP Open Connectors | Open Connectors, OC |
| SAP Event Mesh | Event Mesh, EM, Pub/Sub |
| SAP Cloud Identity Services Authentication | IAS, Identity Auth |
| SAP Cloud Identity Services Authorization | IAS Authorization, Auth Mgmt |
| SAP Cloud Identity Services Identity Lifecycle | IPS, Identity Provisioning |
| SAP Authorization and Trust Management Service | XSUAA, Trust Mgmt |
| SAP HANA Cloud | HANA Cloud, HANA |
| SAP Document Management Service | DMS, Document Mgmt |
| SAP Document Information Extraction | DOX, Document Info Extraction |
| SAP AI Core | AI Core, AICORE |
| SAP AI Launchpad | AI Launchpad |
| SAP Generative AI Hub | GenAI Hub, Joule Studio |
| SAP Joule | Joule, AI Assistant |
| SAP Cloud Logging | Cloud Logging, Logging |
| SAP Cloud ALM | Cloud ALM, ALM |
| SAP Audit Log Service | Audit Log, Audit |
| SAP Alert Notification Service | ANS, Alert Notification |
| SAP Job Scheduling Service | Job Scheduler, Scheduler, Cron |
| SAP Business Application Studio | BAS, App Studio |
| SAP Service Manager | Service Manager, SM |
| SAP Datasphere | Datasphere, DSP, ex-DWC |
| SAP Analytics Cloud | SAC, Analytics Cloud |
| SAP Data Intelligence | Data Intelligence, DI |
| SAP Master Data Integration | MDI, Master Data Integration |
| SAP Cloud Transport Management Service | cTMS, Cloud Transport, TMS |
| SAP Continuous Integration and Delivery | BTP CI/CD, Continuous Integration |
| SAP Mobile Services | Mobile Services, Mobile |
| SAP Task Center | Task Center |
| SAP Start | Start, BTP Start |
| SAP Private Link Service | Private Link, PLS |

## SAP applications (not BTP services, but commonly diagrammed)

| Canonical name | Aliases |
|---|---|
| SAP S/4HANA Cloud | S/4HANA Cloud, S4HC, S/4 Cloud |
| SAP S/4HANA on-premise | S/4HANA on-prem, S/4 |
| SAP S/4HANA Cloud, private edition | S/4HANA PCE, PCE, RISE Private |
| SAP ERP / ECC | ECC, R/3, ERP |
| SAP SuccessFactors | SuccessFactors, SF |
| SAP Ariba | Ariba |
| SAP Fieldglass | Fieldglass, FG |
| SAP Concur | Concur |
| SAP Customer Experience | C4C, CX, Cloud for Customer |
| SAP Commerce Cloud | Commerce, Hybris |
| SAP MDG | Master Data Governance, MDG |

## Italian-specific (FatturaPA / SDI context)

| Concept | Suggested icon (from generic set) |
|---|---|
| Italian SDI (Sistema di Interscambio) | generic/server with label "Italian SDI" |
| AdE (Agenzia delle Entrate) | generic/government building or server with label |
| FatturaPA gateway | integration-suite/Cloud Integration with custom label "FatturaPA Gateway" |

## Maintenance

When a user asks for a service name and `sap-icons-resolve` cannot match it via the index, log the requested name. Periodically (every minor release):

1. Review the log of unmatched requests.
2. For genuinely common ones, add them here.
3. Bump the index schema version when the alias map grows by ≥ 10 entries.
