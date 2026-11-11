import base64
import json
import os
import io
import re
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from django.conf import settings
from .gemini_file_api import upload_to_google_file_api

try:
    import fitz  # PyMuPDF
except ImportError:
    fitz = None

MODELS_TO_TRY = ['gemini-3-flash-preview', 'gemini-flash-latest']

TENDER_PROMPT = """You are an expert procurement document analyst for an Ethiopian import company.

Read the ENTIRE attached tender document carefully, including all pages, tables, annexes, schedules and lot descriptions.

Extract the information into ONE valid JSON object only. Do not include Markdown or explanations.

IMPORTANT LOT EXTRACTION RULES:
1. Extract EVERY distinct lot separately. Never merge different lots.
2. Extract ALL items belonging to each lot. Do not omit items from tables.
3. Each lot may have its own CPO amount or percentage, guarantee requirements, submission deadline and opening schedule. Keep these details attached to the correct lot.
4. Do not assume that all lots share the same dates, times or CPO.
5. If the document has no lot divisions, create one "General" lot and include the identified items.
6. Preserve item names, quantities and units as written.
7. Never invent missing information. Use null for unknown values.
8. Dates must use YYYY-MM-DD. Times must use 24-hour HH:MM format.
9. Distinguish a fixed CPO amount from a percentage. Use AMOUNT or PERCENT.
10. Include guarantee type, validity, issuing bank requirements and other security conditions in guarantee_details when stated.

Use this exact JSON structure:

{
  "bid_reference_number": null,
  "bid_title": null,
  "bid_type": null,
  "purchaser_name": null,
  "organization_type": null,
  "published_date": null,
  "bidder_location": null,
  "contact_phone": null,
  "bid_document_cost_amount": null,
  "bid_document_cost_payment_mode": null,
  "account_holder_name": null,
  "bank_name": null,
  "bank_account_number": null,
  "lots": [
    {
      "lot_name": null,
      "description": null,
      "cpo_amount": null,
      "cpo_type": null,
      "guarantee_details": null,
      "submission_date": null,
      "submission_time": null,
      "opening_date": null,
      "opening_time": null,
      "items": [
        {
          "item_name": null,
          "description": null,
          "quantity": null,
          "unit": null
        }
      ]
    }
  ]
}

Return valid JSON only. Ensure every lot and every identifiable item in the document is included.
"""

ANALYSIS_PROMPT = """
You are Infinity ERP's senior procurement intelligence, tender compliance,
technical evaluation and import-strategy analyst.

MISSION
Read and analyze the ENTIRE attached tender PDF, including every page,
table, schedule, annex, footnote, form and lot. This is a professional
procurement decision-support report for an Ethiopian tender-based importer.

NON-NEGOTIABLE EVIDENCE RULES
1. Never invent a tender requirement, price, supplier, brand, quotation,
   authorization, certificate, deadline, page number or market fact.
2. Separate VERIFIED TENDER FACTS, REGISTERED COMPANY DATA,
   ANALYST RECOMMENDATIONS and UNKNOWN / NOT FOUND information.
3. Every important tender finding must cite its actual PDF page number
   or identifiable section. Never fabricate a page reference.
4. Preserve exact technical values, units, quantities, model numbers,
   standards, tolerances and wording where material.
5. Do not silently resolve contradictions. Explain both conflicting
   statements and identify the page/section for each.
6. Do not call a supplier eligible, authorized, compliant or available
   unless the supplied evidence establishes it.
7. Do not invent current market prices. Mark prices requiring quotations
   as QUOTATION REQUIRED.
8. Analyze each lot independently. Never merge lots or transfer one lot's
   deadline, guarantee or specification to another lot.
9. Identify illegible, missing, incomplete or ambiguous pages and tables.
10. If a field cannot be verified, use UNKNOWN or NOT FOUND.

DEPTH AND COVERAGE
Read the complete document before producing the report. Extract every
identifiable administrative, legal, technical, commercial and delivery
requirement. Include every identifiable item and lot. Do not intentionally
summarize away material conditions.

Return ONE valid JSON object only, with exactly these top-level keys:

{
  "executive_summary": "",
  "tender_facts": [
    {"field":"","value":"","source_page":""}
  ],
  "submission_checklist": [
    {"task":"","mandatory":"YES/NO/UNKNOWN",
     "responsible_document_or_person":"","evidence_or_action":"",
     "source_page":"","risk_if_missed":""}
  ],
  "technical_requirements": [
    {"id":"","lot":"","item":"","requirement":"",
     "exact_tender_specification":"","mandatory":"YES/NO/UNKNOWN",
     "evidence_required":"","supplier_response":"NOT PROVIDED",
     "compliance_status":"NOT VERIFIED","remarks":"",
     "source_page":"","source_section":"","source_excerpt":""}
  ],
  "lot_analysis": [
    {"lot":"","scope_and_items":"","quantities_and_units":"",
     "lot_specific_deadline":"","bid_security_or_cpo":"",
     "technical_conditions":"","commercial_conditions":"",
     "risks_and_open_questions":"","source_page":""}
  ],
  "commercial_analysis": [
    {"topic":"","item_or_lot":"","tender_condition":"",
     "commercial_implication":"","cost_component":"",
     "amount":"","amount_basis":"TOTAL/UNIT/UNKNOWN",
     "cost_inclusion":"INCLUDE/EXCLUDE/UNKNOWN",
     "delivery_basis":"",
     "currency":"","quantity":"","unit":"",
     "unit_cost":"","exchange_rate":"",
     "exchange_rate_source_or_basis":"",
     "calculation_formula":"","calculation_result":"",
     "cost_status":"VERIFIED/USER_INPUT/QUOTATION_REQUIRED/INPUT_REQUIRED/NOT_APPLICABLE",
     "assumption_or_exclusion":"","recommended_action":"",
     "source_page":""}
  ],
  "commercial_gap_register": [
    {"item_or_lot":"","cost_component_or_obligation":"",
     "gap_status":"MISSING/QUOTATION_REQUIRED/UNVERIFIED/RESOLVED/NOT_APPLICABLE",
     "missing_input_or_evidence":"",
     "why_it_matters":"",
     "financial_exposure_amount":"",
     "financial_exposure_currency":"",
     "financial_exposure_basis":"",
     "exposure_status":"QUANTIFIED/UNQUANTIFIED/NOT_APPLICABLE",
     "priority":"CRITICAL/HIGH/MEDIUM/LOW",
     "required_evidence":"",
     "responsible_party":"",
     "recommended_action":"",
     "verification_method":"",
     "delivery_basis":"",
     "source_page":"",
     "related_cost_record":""}
  ],
  "commercial_readiness": [
    {"item_or_lot":"",
     "readiness_status":"READY/CONDITIONALLY_READY/NOT_READY",
     "decision_date":"",
     "decision_basis":"",
     "verified_cost_coverage":"",
     "unresolved_critical_gaps":"",
     "unresolved_high_priority_gaps":"",
     "other_open_gaps":"",
     "blocking_requirements":"",
     "required_actions_before_approval":"",
     "required_evidence":"",
     "responsible_approval_role":"",
     "source_pages":"",
     "decision_limitations":""}
  ],
  "commercial_profitability": [
    {"item_or_lot":"",
     "selling_price_amount":"",
     "selling_price_basis":"TOTAL/UNIT/UNKNOWN",
     "quantity":"",
     "unit":"",
     "currency":"",
     "delivery_basis":"",
     "cost_coverage_status":"COMPLETE/INCOMPLETE/UNKNOWN",
     "verified_cost_evidence":"",
     "selling_price_evidence":"",
     "included_cost_scope":"",
     "gross_profit":"",
     "markup_percentage":"",
     "gross_margin_percentage":"",
     "calculation_status":"CALCULATED/NOT_CALCULATED",
     "calculation_formula":"",
     "assumptions_or_limitations":"",
     "source_pages":""}
  ],
  "brand_sourcing": [
    {"item_or_lot":"","specified_brand_or_standard":"",
     "equivalent_policy":"","manufacturer_or_origin_condition":"",
     "authorization_required":"","sourcing_options_to_investigate":"",
     "verification_needed":"","source_page":""}
  ],
  "model_comparison": [
    {"item_or_lot":"","requirement_id":"","tender_requirement":"",
     "tender_source_page":"","tender_source_section":"",
     "tender_source_excerpt":"",
     "candidate_brand":"","candidate_model":"","manufacturer":"",
     "candidate_specification":"","candidate_specification_value":"",
     "candidate_specification_unit":"",
     "compliance_status":"NOT_VERIFIED",
     "comparison_details":"","deviation_or_gap":"",
     "official_product_url":"","manufacturer_datasheet_url":"",
     "evidence_title":"","evidence_source_type":"",
     "manufacturer_datasheet_page_or_section":"",
     "evidence_access_date":"",
     "candidate_rank":"","ranking_category":"",
     "evidence_quality":"LOW/MEDIUM/HIGH",
     "technical_suitability":"",
     "warranty_and_service_evidence":"",
     "model_lifecycle_status":"CURRENT/DISCONTINUED/UNKNOWN",
     "ranking_explanation":"",
     "recommendation":"","source_page":""}
  ],
  "supplier_strategy": [
    {"supplier_or_supplier_type":"","relevant_scope":"",
     "known_registered_information":"","required_qualification_or_evidence":"",
     "gaps_to_verify":"","recommended_action":""}
  ],
  "supplier_intelligence": [
    {"supplier_name":"","supplier_type":"MANUFACTURER/DISTRIBUTOR/EXPORTER/OTHER",
     "country":"","relevant_item_or_lot":"","products_or_capabilities":"",
     "official_website":"","official_contact_or_sales_url":"",
     "manufacturer_relationship":"","authorization_status":"NOT_VERIFIED",
     "certifications_or_qualifications":"","export_or_delivery_evidence":"",
     "tender_requirement_match":"","verification_status":"NOT_VERIFIED",
     "evidence_title":"","evidence_url":"","evidence_type":"",
     "information_gaps":"","recommended_next_action":""}
  ],
  "bid_strategy": [
    {"priority":"HIGH/MEDIUM/LOW","action":"",
     "reason":"","responsible_role":"",
     "dependency_or_deadline":"","evidence_or_source":""}
  ],
  "risk_register": [
    {"risk":"","likelihood":"LOW/MEDIUM/HIGH/UNKNOWN",
     "impact":"LOW/MEDIUM/HIGH/UNKNOWN",
     "mitigation":"","source_page_or_basis":""}
  ],
  "missing_information": [
    {"missing_or_unclear_information":"","why_it_matters":"",
     "how_to_verify":"","source_page":""}
  ],
  "source_register": [
    {"page_or_section":"","information_supported":""}
  ]
}

ANALYSIS INSTRUCTIONS

A. TENDER FACTS
Identify purchaser, tender title/reference, publication date, procurement
method, document fee, submission location/method, deadline and time,
opening schedule, bid validity, bid security/CPO, delivery location,
delivery period, payment terms, currency and contact details when stated.

B. SUBMISSION CHECKLIST
Identify every required form, eligibility document, registration,
tax/VAT evidence, licenses, audited accounts, experience evidence,
manufacturer authorization, power of attorney, signatures, stamps,
bid security, technical envelope, financial envelope, copies, sealing,
labeling and submission instruction. Only include items supported by
the tender; mark uncertain requirements UNKNOWN.

C. TECHNICAL REQUIREMENTS
Create a separate row for every identifiable requirement and item.

For every requirement:
- Preserve its exact tender wording and technical value.
- Record the actual PDF page number and identifiable section.
- Include a short exact source excerpt supporting the requirement.
- Keep lot, item and requirement ID linked to the source.
- If the page, section or wording cannot be identified reliably,
  state NOT IDENTIFIED rather than inventing a reference.
- Distinguish the tender's printed page number from the PDF page
  number when both are visible.
- Preserve units, tolerances, standards and mandatory conditions.

Preserve exact model, capacity, dimensions, performance, standards,
compatibility, accessories, software, licenses, warranty, installation,
training, documentation and service requirements. Include administrative
requirements in this matrix where they affect compliance.
Do not mark a supplier compliant without its actual response and evidence.

D. LOT ANALYSIS
List every lot independently, its item descriptions, quantities and units,
its own deadline, security, delivery and technical/commercial conditions.
Identify lots with missing item detail or conflicting information.

E. COMMERCIAL AND LANDED-COST ANALYSIS

Analyze each lot and materially different item separately. Extract and
preserve tender-stated quantities, units, currency, price schedule,
delivery location, Incoterms, payment terms, taxes, duties, guarantees,
bid security, performance security, advance payment, retention,
penalties, liquidated damages, warranty, delivery period and price
adjustment conditions. Cite the tender PDF page or section.

COST COMPONENT BREAKDOWN
Where relevant, create separate commercial_analysis records for:
1. Product purchase price / EXW / FOB.
2. International freight.
3. Cargo insurance.
4. CIF or other applicable import-value basis.
5. Customs duty and other import levies.
6. VAT and other applicable taxes.
7. Port, terminal, storage and handling charges.
8. Customs clearance and documentation.
9. Inland transport to the required delivery destination.
10. Bank charges, financing and foreign-exchange costs.
11. Installation, commissioning, training and warranty costs.
12. Other identifiable costs and contingencies, clearly distinguished
    from tender requirements and actual supplier quotations.

CALCULATION RULES
- Calculate only when the necessary values, units, quantity and currency
  are available and their basis is clear.
- Preserve the original currency and amount. Never silently convert.
- For foreign-currency conversion, state the exact exchange rate,
  currency pair, date and source or user-provided basis.
- Distinguish FOB, freight, insurance and CIF. Do not add freight or
  insurance twice when already included in a quoted price.
- Apply duties and taxes only using a verified applicable rate and
  clearly identified taxable base. Do not assume tax treatment.
- Show every formula and its input values. Identify exclusions.
- Calculate total cost and unit cost only when quantity and unit are
  compatible and all required inputs are available.
- Distinguish cost markup from gross profit margin:
  markup = (selling price - cost) / cost;
  gross margin = (selling price - cost) / selling price.
  Never label one as the other.
- Do not invent supplier quotations, market prices, exchange rates,
  tariff rates, tax rates, freight, insurance or customs values.
- If an input is missing, use QUOTATION REQUIRED or INPUT REQUIRED
  and identify exactly what is missing. Do not fabricate a total.
- Clearly distinguish VERIFIED TENDER FACT, VERIFIED EXTERNAL SOURCE,
  USER-PROVIDED INPUT, ESTIMATE and ANALYST RECOMMENDATION.
- Do not present an estimate as an actual supplier quotation or
  a calculated figure as a tender-stated amount.
- Identify whether a figure is per item, per unit, per shipment,
  per lot or for the complete contract.
- For every cost amount, classify amount_basis as:
  TOTAL when it is a documented total for the stated quantity;
  UNIT when it is a documented price per stated unit;
  UNKNOWN when the basis cannot be established.
- Calculate unit_cost only when the amount basis is established:
  UNIT: unit_cost equals the documented amount.
  TOTAL: unit_cost equals total amount divided by a verified,
  positive quantity using the same unit and cost scope.
- Classify cost_inclusion as INCLUDE only for a distinct,
  supported cost belonging in the bid-cost calculation.
  Use EXCLUDE for duplicated costs, costs already included
  elsewhere, or costs outside the bid scope. Use UNKNOWN
  when inclusion cannot be established. Never default to INCLUDE.
- Record delivery_basis exactly as documented. Do not infer it.
- Keep summaries separate by item/lot, currency and delivery basis.
- TOTAL amounts may contribute once. UNIT amounts may contribute
  only when multiplied by a verified positive matching quantity.
  UNKNOWN basis or missing inputs must not contribute to totals.
- Never double-count freight, insurance, CIF, taxes or components
  already included in another quoted amount.
  UNKNOWN: leave unit_cost blank and explain the missing basis.
- Never divide by zero, infer a quantity, or calculate using
  incompatible currencies, units, lots or delivery bases.
- Preserve the original amount and currency. Do not silently
  convert currencies or treat text descriptions as numeric values.
- Do not combine different lots, currencies, units or delivery bases.

COMMERCIAL DECISION
Explain the financial implications, cash-flow exposure, payment and
guarantee obligations, foreign-exchange risk, delivery exposure and
pricing risks. Identify the exact missing quotations or inputs needed
before a defensible bid price can be prepared.

COMMERCIAL MISSING-INPUT RECONCILIATION

Create a separate commercial_gap_register record for every material
missing, quoted-but-unverified, unresolved or non-applicable cost
component or commercial obligation.

Review each lot independently. Consider:
- Supplier product quotation and price validity.
- Freight, cargo insurance and delivery to destination.
- Incoterms and the precise delivery basis.
- Customs duty, import levies, VAT and other applicable taxes.
- Customs clearance, documentation, port and storage charges.
- Inland transport, installation and commissioning.
- Bank charges, financing, foreign-exchange costs and payment terms.
- Bid security, performance security, advance guarantees and retention.
- Warranty, spare parts, training, service and after-sales obligations.
- Delivery deadlines, penalties, liquidated damages and price adjustment.

STATUS RULES
- MISSING: a required input or document has not been supplied.
- QUOTATION_REQUIRED: an actual supplier or service-provider quotation
  is needed before pricing can be established.
- UNVERIFIED: information exists but reliable supporting evidence
  or confirmation is insufficient.
- RESOLVED: the required information and supporting evidence are recorded.
- NOT_APPLICABLE: the requirement is demonstrably outside the relevant
  lot or commercial scope. Explain the basis.

FINANCIAL EXPOSURE RULES
- Quantify exposure only when an actual supported amount and currency
  are available and the calculation basis is clear.
- Otherwise leave financial_exposure_amount blank, set
  exposure_status to UNQUANTIFIED, and explain what is needed.
- Never enter zero to represent an unknown cost.
- Never invent prices, quotations, rates, taxes, freight or exposure.
- Do not duplicate a cost already included in another amount.
- Keep lots, currencies, units and delivery bases separate.
- Link the gap to its related cost record where an identifiable
  cost component exists.

PRIORITY RULES
- CRITICAL: a gap that prevents defensible bid pricing or creates
  a material eligibility, guarantee, delivery or submission risk.
- HIGH: a material financial or contractual uncertainty requiring
  resolution before bid approval.
- MEDIUM: important but not immediately blocking information.
- LOW: minor clarification or documentation improvement.

For every gap, state exactly what evidence is needed, who should
provide it when identifiable, how it should be verified, and the
recommended next action. Cite the relevant tender PDF page or section
where applicable. Distinguish tender requirements, user inputs,
supplier quotations, externally verified facts and analyst assessments.
Do not mark a gap RESOLVED merely because an action is recommended.

COMMERCIAL BID READINESS DECISION — LOT BY LOT

Create one commercial_readiness record for every tender lot. If the
tender has no lot divisions, create one record for the General scope.
Never combine different lots, currencies, delivery bases or cost scopes.

Use exactly these decision statuses:

READY:
- No unresolved CRITICAL or HIGH commercial gaps remain.
- Required product and service quotations or other pricing evidence
  are available and sufficiently verified.
- Required cost components have a documented inclusion/exclusion basis.
- Quantities, units, currencies, delivery basis and applicable exchange
  rate basis are sufficiently established for defensible bid pricing.
- Material tender payment, guarantee, delivery, warranty and penalty
  obligations have been reviewed and can be met based on documented
  evidence or confirmed company capability.
- No unresolved mandatory commercial condition prevents bid approval.

CONDITIONALLY_READY:
- No unresolved CRITICAL blocker remains.
- One or more manageable HIGH, MEDIUM or LOW gaps remain, or a
  material quotation or confirmation is pending.
- State every outstanding action, required evidence, responsible
  role and condition that must be satisfied before final approval.
- This status is not authorization to submit an unverified bid price.

NOT_READY:
- Any unresolved CRITICAL gap exists.
- A mandatory cost input or commercial obligation prevents defensible
  pricing or a reliable bid decision.
- A required quotation, guarantee, delivery commitment, tax basis,
  payment condition or other material evidence remains unavailable
  where it is necessary to establish bid feasibility.
- A material unresolved contradiction makes the lot's commercial
  obligations or cost basis unreliable.

DECISION INTEGRITY
1. Assess each lot independently using commercial_analysis,
   commercial_gap_register, lot_analysis and tender facts.
2. A gap is resolved only when adequate evidence is actually recorded.
3. An unknown amount is never zero. Do not treat missing cost records
   as evidence that a cost does not apply.
4. Do not declare READY merely because no gap records were generated.
   First establish whether the relevant commercial cost and obligation
   categories were actually reviewed.
5. NOT_APPLICABLE requires a documented reason.
6. Do not invent a financial readiness score, profit margin, quotation,
   exchange rate, cost coverage percentage or approval.
7. Distinguish verified amounts from user inputs, estimates and
   quotations still required.
8. Do not mark a lot READY if a mandatory technical or eligibility
   issue makes commercial bid approval unsafe; identify that dependency.
9. State the exact gaps and evidence supporting the decision.
10. If the evidence is insufficient to determine readiness, use
    NOT_READY and explain the unresolved assessment limitation.

For each lot record:
- readiness_status: exact status from the three allowed values.
- decision_basis: concise explanation grounded in available records.
- verified_cost_coverage: describe which material cost categories
  have documented and verified inputs; do not invent percentages.
- unresolved_critical_gaps and unresolved_high_priority_gaps:
  identify actual unresolved gaps, or state NONE IDENTIFIED only
  when the records support that conclusion.
- other_open_gaps: list remaining medium/low gaps.
- blocking_requirements: identify what prevents approval.
- required_actions_before_approval: concrete actions, not generic advice.
- required_evidence: exact documents or confirmations still needed.
- responsible_approval_role: suggested company role, clearly a
  recommendation rather than an actual approval.
- source_pages: relevant tender PDF pages or sections.
- decision_limitations: explain missing or insufficient evidence.

This is a decision-support recommendation, not an actual management
approval or authorization to submit a bid.

COMMERCIAL PROFITABILITY AND MARGIN ANALYSIS

Create one commercial_profitability record for every tender lot.
If there are no lot divisions, create one record for General scope.
Never combine different lots, currencies, units or delivery bases.

SELLING PRICE EVIDENCE
- Use only an explicitly documented selling price from a tender
  price schedule or clearly identified user-provided input.
- Record its amount, currency, basis, quantity, unit and evidence.
- selling_price_basis must be TOTAL, UNIT or UNKNOWN.
- TOTAL means the price covers the documented total quantity.
- UNIT means the price is for one documented unit.
- UNKNOWN means the price basis cannot be established.
- Never invent a proposed selling price.
- Never treat a supplier cost quotation as a selling price.

COST COVERAGE
- COMPLETE only when every material cost component for the lot
  and delivery basis has been reviewed and its inclusion or
  exclusion is supported by evidence.
- INCOMPLETE when material costs are missing, unverified,
  awaiting quotations or unresolved.
- UNKNOWN when available evidence cannot establish coverage.
- An empty cost list is not proof of complete cost coverage.
- Use only explicit INCLUDE cost records with matching lot,
  currency and delivery basis.
- Excluded costs require a supported reason.
- Never treat unknown costs as zero or count costs twice.

CALCULATION RULES
- For TOTAL selling price, use the documented total revenue.
- For UNIT selling price, multiply documented unit price by
  verified positive matching quantity.
- Total included cost must use the same lot, currency and
  delivery basis as the selling price.
- Gross profit = total selling revenue - total included cost.
- Markup percentage = (gross profit / total included cost) * 100.
- Gross margin percentage = (gross profit / total selling revenue) * 100.
- Never confuse markup with gross margin.
- Do not calculate markup when cost is zero or unavailable.
- Do not calculate gross margin when revenue is zero or unavailable.
- If cost coverage is INCOMPLETE or UNKNOWN, or any required
  input/evidence is missing, set calculation_status to
  NOT_CALCULATED and leave all three result fields blank.
- Never use zero to represent missing information.
- Show formulas, cost scope, evidence and limitations.
- Distinguish tender-stated prices from user-provided prices.
- These are gross profitability figures, not net profit.
  Do not imply that overhead, financing, income tax or other
  excluded expenses have been accounted for.
- These analytical results are not management approval or
  authorization to submit a bid.

F. BRAND, MODEL AND LIVE TECHNICAL RESEARCH

This is a major procurement research task, not a generic list of famous brands.

1. Read the extracted technical requirements for every item and lot.
2. Use Google Search grounding to research actual manufacturers, brands,
   product families and specific model numbers that could satisfy those
   requirements.
3. Prioritize official manufacturer product pages, official datasheets,
   technical manuals, catalogues and official product documentation.
   Use authorized distributor documentation only where manufacturer
   documentation is unavailable. Clearly identify the source type.
4. Identify up to three genuinely relevant candidate models per item
   where sufficient candidates exist. Do not invent candidates merely
   to fill a comparison table.
5. Compare each candidate against EACH identifiable mandatory tender
   parameter: capacity, dimensions, ratings, performance, materials,
   standards, compatibility, accessories, software, warranty, service
   and all other applicable requirements.
6. Preserve the exact tender requirement and the candidate's published
   value, including units. Do not replace exact values with vague
   descriptions such as "high performance" or "equivalent quality".
7. Assign only these compliance statuses:
   COMPLIANT: all identifiable mandatory requirements are supported
   by reliable evidence and no deviation is identified.
   PARTIAL: at least one known requirement is not met or differs.
   NON_COMPLIANT: a mandatory requirement is demonstrably not met.
   NOT_VERIFIED: evidence is insufficient to establish compliance.
8. Never infer compliance from brand reputation, product family,
   approximate capacity or a similar model number.
9. Record the exact candidate model, manufacturer, official product
   URL, datasheet URL, evidence title, technical values and deviations.
10. If a datasheet cannot be found, say NOT FOUND. If a specification
    is not published, say NOT PUBLISHED. Do not manufacture values.
11. Distinguish current models from discontinued or obsolete models.
12. Rank candidate models separately for each tender item or lot.
    Use one consistent candidate_rank sequence, starting at 1, and
    do not compare unrelated product categories or different lots.
13. Apply this ranking order before considering any secondary advantage:
    a. COMPLIANT: all identifiable mandatory requirements are met
       and supported by reliable, traceable evidence.
    b. PARTIAL: at least one known requirement has a deviation or gap.
    c. NON_COMPLIANT: a mandatory requirement is demonstrably failed.
    d. NOT_VERIFIED: evidence is insufficient to establish compliance.
    A candidate with an unverified mandatory requirement must never
    be presented as fully compliant.
14. Within the same compliance category, prioritize:
    a. Completeness, reliability and traceability of manufacturer evidence.
    b. Technical suitability against the tender requirements.
    c. Documented warranty, after-sales service and support.
    d. Verified current/discontinued lifecycle status.
15. Do not let a high secondary score, brand reputation, low price or
    attractive feature override a mandatory technical failure.
16. Explain each candidate's rank using specific evidence and gaps.
    If candidates cannot be fairly ranked, state NOT RANKABLE and
    explain why. Do not invent numerical scores or missing facts.
17. Distinguish current models from discontinued or obsolete models.
18. If the tender names a specific brand/model, respect the tender's
    stated equivalent policy. Do not silently substitute alternatives.
19. Identify manufacturer authorization, country-of-origin and
    certification requirements separately from technical compliance.
20. Search for each materially different product; do not reuse one
    product's datasheet or specifications for another model.

For each candidate model, create separate model_comparison records
covering EACH relevant tender requirement. Never combine several
different mandatory parameters into one vague comparison.

Every comparison record must include:
- The exact tender requirement and requirement ID.
- Tender PDF page, identifiable section and a short supporting
  tender source excerpt.
- Candidate brand, exact model and manufacturer.
- The candidate's exact published specification, preserving its
  numerical value and unit where applicable.
- The comparison result and precise deviation or gap, if any.
- Official product page URL and manufacturer datasheet URL where found.
- Evidence title and evidence source type, such as
  MANUFACTURER_DATASHEET, OFFICIAL_PRODUCT_PAGE,
  OFFICIAL_MANUAL or AUTHORIZED_DISTRIBUTOR_DOCUMENT.
- Datasheet page or section containing the supporting specification.
- Evidence access date when available.

Keep tender source references separate from manufacturer evidence
references. A tender page number is not a manufacturer datasheet page.

Never invent excerpts, page numbers, sections, specification values,
units, URLs or access dates. If an evidence location cannot be
identified, state NOT IDENTIFIED. If a source or specification is
unavailable, state NOT FOUND or NOT PUBLISHED as applicable.

A search-result snippet alone is not sufficient proof of a
manufacturer specification. If the original evidence cannot be
examined, retain NOT_VERIFIED and explain the limitation.

G2. LIVE RESEARCH EVIDENCE
Include a source_register entry for each useful external source,
with its title, URL, source type, information supported and access
date where available. Separate manufacturer evidence from distributor
claims and general web sources. A search result alone does not prove
that a product meets a requirement.

If Google Search returns no reliable supporting source for a candidate,
retain NOT_VERIFIED and explain the missing evidence.

G. SUPPLIER STRATEGY
Use the registered supplier context provided separately. Match only
documented supplier capabilities to tender scope. Identify missing
catalogues, authorization, references, certificates, quotations,
delivery commitments, warranty and after-sales evidence.
If no matching registered supplier is documented, say so.

G2. LIVE SUPPLIER, MANUFACTURER AND DISTRIBUTOR RESEARCH

Use Google Search grounding to identify real companies relevant to the
actual products, brands, models and lots identified in this tender.

1. Research manufacturers, authorized distributors, exporters and
   suppliers whose documented products or capabilities match the tender.
2. Prioritize official manufacturer websites, official distributor
   directories, company websites, product catalogues and official
   certification or authorization evidence.
3. Identify the supplier's country, relevant product range, official
   website and available official sales/contact page.
4. Distinguish manufacturers from distributors, exporters, agents and
   general trading companies.
5. Record manufacturer relationships and authorization status only
   when supported by evidence. Otherwise use NOT_VERIFIED.
6. Assess relevance against the tender's actual item, technical
   requirements, quantities, delivery obligations and qualification
   conditions. Do not assume a supplier can meet an obligation merely
   because it sells a related product.
7. Record evidence titles, URLs and evidence types. Clearly distinguish
   official company evidence from third-party directories or claims.
8. Identify missing authorization letters, certifications, references,
   export records, quotations, delivery commitments, warranty and
   after-sales evidence.
9. Recommend practical verification or contact actions for promising
   candidates. Do not invent email addresses, telephone numbers,
   certifications, authorization, stock, prices or delivery capacity.
10. If reliable evidence cannot establish a supplier's identity,
    capabilities or relationship to a manufacturer, state the gap
    explicitly and retain NOT_VERIFIED.
11. Do not present a company as an approved, authorized or qualified
    tender supplier unless the relevant evidence supports that claim.
12. Return distinct, tender-relevant candidates rather than generic
    lists of famous international companies.

H. BID STRATEGY
Produce prioritized practical actions for the Bid Manager, Bid Operator,
Finance and management. Identify dependencies and deadline-sensitive
actions. Do not make unsupported claims about probability of winning.

I. RISK REGISTER AND INFORMATION GAPS
Identify disqualification risks, ambiguous specifications, short lead
times, security exposure, delivery and warranty obligations, currency
and import risks, missing evidence and clarification questions.

OUTPUT QUALITY
Be detailed, structured and commercially useful. Prefer complete
requirement coverage over a short generic summary. Use concise factual
entries, not vague advice. Return valid JSON only, without Markdown fences.
"""



def _api_key():
    key = os.environ.get('GEMINI_API_KEY', '') or getattr(settings, 'GEMINI_API_KEY', '')
    if key in ('', 'PASTE_YOUR_GEMINI_API_KEY_HERE'):
        return ''
    return key

def compress_pdf_if_needed(file_obj, max_size_mb=3.5):
    if fitz is None:
        return _get_file_bytes(file_obj)

    try:
        if hasattr(file_obj, 'storage'):
            file_obj.open()
            raw = file_obj.read()
            file_obj.close()
        elif hasattr(file_obj, 'read'):
            file_obj.seek(0)
            raw = file_obj.read()
        else:
            with open(file_obj, 'rb') as f:
                raw = f.read()

        if len(raw) <= max_size_mb * 1024 * 1024:
            return raw

        doc = fitz.open(stream=raw, filetype="pdf")
        out_buffer = io.BytesIO()
        doc.save(out_buffer, garbage=4, deflate=True)
        doc.close()
        return out_buffer.getvalue()
    except Exception as e:
        print(f"Compression failed: {e}")
        return _get_file_bytes(file_obj)

def _get_file_bytes(file_obj):
    try:
        if hasattr(file_obj, 'storage'):
            file_obj.open()
            raw = file_obj.read()
            file_obj.close()
            return raw
        elif hasattr(file_obj, 'read'):
            file_obj.seek(0)
            return file_obj.read()
        else:
            with open(file_obj, 'rb') as f:
                return f.read()
    except Exception as e:
        raise Exception(f"Could not read file: {str(e)}")

def _send_to_gemini(
    file_obj, prompt, google_search=False, return_metadata=False
):
    key = _api_key()
    if not key:
        return "GEMINI_API_KEY not set. Configure it in Render Environment Variables."

    try:
        raw = compress_pdf_if_needed(file_obj, max_size_mb=3.5)
    except Exception as exc:
        return f"Could not read tender PDF: {exc}"

    if not raw:
        return "Tender PDF is empty."

    if len(raw) > 4 * 1024 * 1024:
        try:
            uri, error = upload_to_google_file_api(raw, "tender_document.pdf")
            if error:
                return f"File API Error: {error}"
            body = {
                "contents": [{
                    "parts": [
                        {"text": prompt},
                        {"file_data": {
                            "mime_type": "application/pdf",
                            "file_uri": uri
                        }}
                    ]
                }]
            }
        except Exception as exc:
            return f"Failed to upload large PDF: {exc}"
    else:
        encoded = base64.b64encode(raw).decode("ascii")
        body = {
            "contents": [{
                "parts": [
                    {"text": prompt},
                    {"inline_data": {
                        "mime_type": "application/pdf",
                        "data": encoded
                    }}
                ]
            }]
        }

    # Google Search grounding is enabled only for the deep War Room
    # analysis. The legacy generateContent endpoint must not combine
    # google_search with JSON response MIME mode.
    body["generationConfig"] = {
        "temperature": 0.15,
        "maxOutputTokens": 24000
    }
    if google_search:
        body["tools"] = [{"google_search": {}}]
    else:
        body["generationConfig"]["response_mime_type"] = "application/json"

    session = requests.Session()
    retry = Retry(
        connect=3,
        read=3,
        backoff_factor=1,
        status_forcelist=[429, 500, 502, 503, 504]
    )
    session.mount("https://", HTTPAdapter(max_retries=retry))
    headers = {"User-Agent": "Infinity-ERP-Procurement-Analyst"}

    last_error = "Unknown Gemini error"

    for model in MODELS_TO_TRY:
        url = (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"{model}:generateContent"
        )
        try:
            response = session.post(
                url,
                params={"key": key},
                json=body,
                headers=headers,
                timeout=240
            )

            if response.status_code == 200:
                payload = response.json()
                candidates = payload.get("candidates") or []
                if not candidates:
                    last_error = (
                        payload.get("promptFeedback", {}).get("blockReason")
                        or "Gemini returned no analysis candidate."
                    )
                    continue

                candidate = candidates[0]
                parts = candidate.get("content", {}).get("parts", [])
                result = "\n".join(
                    part.get("text", "")
                    for part in parts
                    if part.get("text")
                ).strip()

                if not result:
                    last_error = (
                        "Gemini returned an empty response. Finish reason: "
                        + str(candidate.get("finishReason", "UNKNOWN"))
                    )
                    continue

                if candidate.get("finishReason") == "MAX_TOKENS":
                    last_error = (
                        "Gemini response reached its output limit. "
                        "The analysis may be incomplete; retry or use a smaller PDF."
                    )
                    continue

                result = re.sub(
                    r"^```(?:json)?\s*|\s*```$",
                    "",
                    result,
                    flags=re.IGNORECASE
                ).strip()

                if return_metadata and result.startswith("{"):
                    try:
                        structured = json.loads(result)
                        grounding = candidate.get("groundingMetadata") or {}
                        sources = []

                        for chunk in grounding.get("groundingChunks", []):
                            web_source = chunk.get("web") or {}
                            uri = web_source.get("uri")
                            if uri:
                                sources.append({
                                    "title": web_source.get("title", ""),
                                    "url": uri,
                                    "source_type": "GOOGLE_SEARCH_GROUNDING"
                                })

                        structured["_web_research"] = {
                            "status": (
                                "GROUNDED" if sources
                                else "NO_GROUNDED_SOURCES"
                            ),
                            "search_enabled": True,
                            "search_queries": grounding.get(
                                "webSearchQueries", []
                            ),
                            "sources": sources
                        }
                        result = json.dumps(
                            structured, ensure_ascii=False
                        )
                    except (ValueError, TypeError):
                        pass

                return result

            last_error = (
                f"Gemini HTTP {response.status_code}: "
                f"{response.text[:1200]}"
            )
            if response.status_code not in (404, 429, 500, 502, 503, 504):
                break

        except Exception as exc:
            last_error = str(exc)

    return f"AI analysis failed. {last_error}"



def extract_tender_from_pdf(file_obj):
    result_text = _send_to_gemini(file_obj, TENDER_PROMPT)
    if result_text.startswith("{"):
        try:
            return {'data': json.loads(result_text)}
        except Exception as e:
            return {'error': f"JSON parse error: {e}. Raw: {result_text[:100]}"}
    return {'error': result_text}

def analyze_bid_document(file_obj, supplier_context=""):
    prompt = ANALYSIS_PROMPT
    if supplier_context:
        prompt += (
            "\n\nREGISTERED COMPANY SUPPLIER RECORDS "
            "(REFERENCE DATA ONLY; NOT PROOF OF ELIGIBILITY):\n"
            + supplier_context
            + "\nUse these records only to identify possible matches and "
              "evidence gaps. Do not invent capabilities, quotations, "
              "authorization, stock or delivery commitments."
        )

    result = _send_to_gemini(
        file_obj,
        prompt,
        google_search=True,
        return_metadata=True
    )
    error_prefixes = (
        "GEMINI_API_KEY",
        "Could not read",
        "Tender PDF is empty",
        "File API Error",
        "Failed to upload",
        "AI analysis failed",
        "Gemini Error",
        "All AI models failed",
    )
    if result.startswith(error_prefixes):
        raise RuntimeError(result)

    return result


def _display_value(value):
    if value is None:
        return "UNKNOWN"
    if isinstance(value, bool):
        return "YES" if value else "NO"
    if isinstance(value, (str, int, float)):
        return str(value)
    if isinstance(value, list):
        return "; ".join(_display_value(item) for item in value)
    if isinstance(value, dict):
        return "; ".join(
            f"{key}: {_display_value(val)}"
            for key, val in value.items()
        )
    return str(value)


def _format_records(records, fields):
    if not isinstance(records, list):
        return _display_value(records) if records else ""
    output = []
    for index, record in enumerate(records, 1):
        if isinstance(record, dict):
            lines = []
            for key, label in fields:
                value = record.get(key)
                if value not in (None, "", [], {}):
                    lines.append(f"{label}: {_display_value(value)}")
            if lines:
                output.append(f"{index}. " + "\n   ".join(lines))
        elif record:
            output.append(f"{index}. {_display_value(record)}")
    return "\n\n".join(output)


def parse_war_room_output(text):
    """
    Accept the current structured JSON response and legacy section headings.
    Convert structured technical requirements into an editable CSV matrix.
    """
    import csv
    import json
    import re

    if not text or not str(text).strip():
        raise ValueError("Gemini returned an empty analysis.")

    cleaned = str(text).strip()
    cleaned = re.sub(
        r"^```(?:json|text)?\s*|\s*```$",
        "",
        cleaned,
        flags=re.IGNORECASE
    ).strip()

    try:
        data = json.loads(cleaned)
    except (json.JSONDecodeError, TypeError):
        data = None

    if isinstance(data, dict):
        summary_parts = []

        executive = data.get("executive_summary")
        if executive:
            summary_parts.append("EXECUTIVE SUMMARY\n" + _display_value(executive))

        summary_fields = [
            ("tender_facts", "TENDER FACTS", [
                ("field", "Field"), ("value", "Value"),
                ("source_page", "Source page")
            ]),
            ("lot_analysis", "LOT-BY-LOT ANALYSIS", [
                ("lot", "Lot"), ("scope_and_items", "Scope and items"),
                ("quantities_and_units", "Quantities and units"),
                ("lot_specific_deadline", "Deadline"),
                ("bid_security_or_cpo", "Bid security / CPO"),
                ("technical_conditions", "Technical conditions"),
                ("commercial_conditions", "Commercial conditions"),
                ("risks_and_open_questions", "Risks / questions"),
                ("source_page", "Source page")
            ]),
            ("commercial_analysis", "COMMERCIAL ANALYSIS", [
                ("topic", "Topic"), ("tender_condition", "Tender condition"),
                ("commercial_implication", "Implication"),
                ("recommended_action", "Recommended action"),
                ("source_page", "Source page")
            ]),
            ("bid_strategy", "RECOMMENDED BID STRATEGY", [
                ("priority", "Priority"), ("action", "Action"),
                ("reason", "Reason"), ("responsible_role", "Responsible role"),
                ("dependency_or_deadline", "Dependency / deadline"),
                ("evidence_or_source", "Evidence / source")
            ]),
            ("risk_register", "RISK REGISTER", [
                ("risk", "Risk"), ("likelihood", "Likelihood"),
                ("impact", "Impact"), ("mitigation", "Mitigation"),
                ("source_page_or_basis", "Source / basis")
            ]),
            ("missing_information", "MISSING INFORMATION AND CLARIFICATIONS", [
                ("missing_or_unclear_information", "Missing information"),
                ("why_it_matters", "Why it matters"),
                ("how_to_verify", "How to verify"),
                ("source_page", "Source page")
            ]),
            ("source_register", "SOURCE REGISTER", [
                ("page_or_section", "Page / section"),
                ("information_supported", "Information supported")
            ]),
        ]

        for key, heading, fields in summary_fields:
            rendered = _format_records(data.get(key, []), fields)
            if rendered:
                summary_parts.append(heading + "\n" + rendered)

        checklist = _format_records(
            data.get("submission_checklist", []),
            [
                ("task", "Required action"),
                ("mandatory", "Mandatory"),
                ("responsible_document_or_person", "Responsible document/person"),
                ("evidence_or_action", "Evidence/action"),
                ("source_page", "Source page"),
                ("risk_if_missed", "Risk if missed")
            ]
        )

        brands = _format_records(
            data.get("brand_sourcing", []),
            [
                ("item_or_lot", "Item / lot"),
                ("specified_brand_or_standard", "Specified brand / standard"),
                ("equivalent_policy", "Equivalent policy"),
                ("manufacturer_or_origin_condition", "Manufacturer / origin"),
                ("authorization_required", "Authorization"),
                ("sourcing_options_to_investigate", "Sourcing leads to verify"),
                ("verification_needed", "Verification required"),
                ("source_page", "Source page")
            ]
        )

        suppliers = _format_records(
            data.get("supplier_strategy", []),
            [
                ("supplier_or_supplier_type", "Supplier"),
                ("relevant_scope", "Relevant scope"),
                ("known_registered_information", "Registered information"),
                ("required_qualification_or_evidence", "Required evidence"),
                ("gaps_to_verify", "Gaps to verify"),
                ("recommended_action", "Recommended action")
            ]
        )

        compliance_rows = data.get("technical_requirements", [])
        csv_buffer = io.StringIO()
        writer = csv.writer(csv_buffer)
        headers = [
            "Requirement ID", "Lot", "Item", "Requirement",
            "Tender Specification", "Mandatory", "Evidence Required",
            "Supplier Response", "Compliance Status", "Remarks",
            "Source Page"
        ]
        writer.writerow(headers)

        if isinstance(compliance_rows, list):
            for row in compliance_rows:
                if not isinstance(row, dict):
                    continue
                writer.writerow([
                    _display_value(row.get("id", "")),
                    _display_value(row.get("lot", "")),
                    _display_value(row.get("item", "")),
                    _display_value(row.get("requirement", "")),
                    _display_value(row.get("exact_tender_specification", "")),
                    _display_value(row.get("mandatory", "UNKNOWN")),
                    _display_value(row.get("evidence_required", "")),
                    _display_value(row.get("supplier_response", "NOT PROVIDED")),
                    _display_value(row.get("compliance_status", "NOT VERIFIED")),
                    _display_value(row.get("remarks", "")),
                    _display_value(row.get("source_page", ""))
                ])

        result = {
            "summary": "\n\n".join(summary_parts),
            "checklist": checklist,
            "compliance": csv_buffer.getvalue().strip(),
            "brands": brands,
            "suppliers": suppliers,
            "structured_data": data,
        }

        if not any(result.values()):
            raise ValueError("AI response contained no usable analysis sections.")
        return result

    # Backward compatibility for previously generated heading-based output.
    keys = {
        "SUMMARY": "summary",
        "CHECKLIST": "checklist",
        "COMPLIANCE CSV": "compliance",
        "COMMERCIAL": "commercial",
        "BRANDS": "brands",
        "SUPPLIERS": "suppliers",
        "SOURCES": "sources",
    }
    result = {value: "" for value in keys.values()}
    result["structured_data"] = {}
    current = None
    collected = {value: [] for value in keys.values()}

    for line in cleaned.splitlines():
        heading = line.strip().strip("-").strip().upper()
        if heading in keys:
            current = keys[heading]
            continue
        if current:
            collected[current].append(line)

    for key, lines in collected.items():
        result[key] = "\n".join(lines).strip()

    if not any(result.values()):
        raise ValueError(
            "AI response was not valid structured JSON or recognized analysis sections."
        )

    result["summary"] = (
        result.get("summary", "")
        + "\n\nCOMMERCIAL REVIEW\n" + result.get("commercial", "")
        + "\n\nSOURCE REFERENCES\n" + result.get("sources", "")
    ).strip()
    return result



def ask_infinity_brain(question, context_data):
    key = _api_key()
    if not key: return "API key missing."
    prompt = f"Company Data:\n{context_data}\n\nQuestion: {question}\nAnswer concisely:"
    body = {'contents': [{'parts': [{'text': prompt}]}]}
    session = requests.Session()
    for model in MODELS_TO_TRY:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        try:
            r = session.post(url, params={'key': key}, json=body, timeout=60)
            if r.status_code == 200:
                return r.json()['candidates'][0]['content']['parts'][0]['text']
        except: continue
    return "AI is busy."

def predict_winning_price(tender, historical_context):
    key = _api_key()
    if not key: return "API key missing."
    prompt = f"Tender: {tender.bid_title}\nCost: {tender.total_cost}\nHistory: {historical_context}\nSuggest a winning price:"
    body = {'contents': [{'parts': [{'text': prompt}]}]}
    session = requests.Session()
    for model in MODELS_TO_TRY:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        try:
            r = session.post(url, params={'key': key}, json=body, timeout=60)
            if r.status_code == 200:
                return r.json()['candidates'][0]['content']['parts'][0]['text']
        except: continue
    return "AI is busy."
