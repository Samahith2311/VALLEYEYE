# Flood Rescue Mapping & Situation Intelligence System

## 1. Project Overview

Build a web-based geospatial intelligence system that helps rescue teams understand the impact of a flood using satellite imagery, open geographic data, road-network analysis, and an AI component.

The system must accept:

- A geographic **area of interest (AOI)**
- A **flood/event date**

It must then analyze satellite imagery and geographic datasets to answer three critical rescue questions:

1. **Where did the flood hit?**
2. **What was damaged?**
3. **Who is cut off?**

The system will be demonstrated using the **August 2026 Trishuli flood** as the primary case study and compared against the official **Copernicus Emergency Management Service activation EMSR927** products.

---

# 2. Core Requirements

## 2.1 Area and Flood-Date Input

The application must allow users to define:

- Area of interest
- Flood/event date
- Optional map-based selection of the affected region

The system should automatically identify suitable satellite imagery from before and after the selected flood event.

### Required Inputs

```text
Area of Interest
Flood/Event Date
```

### Example

```text
Area: Trishuli Valley, Nepal
Flood Date: August 2026
```

---

# 3. Requirement 1 — Where Did the Flood Hit?

The system must identify and map areas affected by flooding and debris.

## 3.1 Satellite Data

### Sentinel-1

Use Sentinel-1 Synthetic Aperture Radar (SAR) imagery as the primary flood-detection source.

Reason:

- Radar can acquire imagery through clouds.
- It is suitable for monsoon conditions.
- It can provide useful flood information when optical imagery is unavailable.

### Sentinel-2

Use Sentinel-2 optical imagery when cloud conditions allow.

Possible uses:

- Visual confirmation
- Surface characterization
- Debris identification
- Pre/post-event comparison

---

## 3.2 Before/After Comparison

The system must compare:

```text
Pre-Flood Image
       ↓
Flood Event
       ↓
Post-Flood Image
```

The analysis should identify significant changes associated with:

- Flooded areas
- Water expansion
- Debris-covered areas
- Changed land surfaces

---

## 3.3 Flood/Debris Map

The application must display the detected impact on an interactive map.

Suggested layers:

```text
Satellite Base Layer
        +
Pre-Flood Imagery
        +
Post-Flood Imagery
        +
Flood Extent
        +
Debris/Change Areas
```

The system should provide:

- Flooded-area boundaries
- Affected-area extent
- Area statistics
- Map visualization
- Before/after comparison

---

# 4. Requirement 2 — What Was Damaged?

The system must overlay geographic infrastructure data with the detected flood extent.

## 4.1 OpenStreetMap Data

Use OpenStreetMap (OSM) data for:

- Buildings
- Roads
- Bridges
- Settlements
- Other relevant infrastructure where available

---

## 4.2 Infrastructure Impact Estimation

The system must spatially compare infrastructure with the detected flood/debris areas.

### Example

```text
Flood Extent
     +
OSM Buildings
     ↓
Potentially Affected Buildings
```

```text
Flood Extent
     +
OSM Roads
     ↓
Potentially Affected Road Segments
```

```text
Flood/Change Area
     +
OSM Bridges
     ↓
Potentially Affected Bridges
```

---

## 4.3 Required Outputs

The system should estimate:

- Number of potentially affected buildings
- Length/number of potentially affected roads
- Number of potentially affected bridges
- Affected infrastructure locations

> These values represent geospatial impact estimates derived from the system and should not be presented as confirmed physical damage unless independently verified.

---

# 5. Requirement 3 — Who Is Cut Off?

The system must analyze road connectivity to identify settlements that may have lost road access.

## 5.1 Road Network

Use the available road network to construct a graph:

```text
Roads → Nodes + Edges → Connectivity Graph
```

The graph should represent:

- Roads as edges
- Intersections/locations as nodes
- Settlements as connected locations
- Towns/hospitals as important destination nodes

---

## 5.2 Connectivity Analysis

The system must identify settlements that no longer have a viable road connection to the nearest:

- Town
- Hospital

Potentially affected road segments detected from the flood analysis should be considered unavailable/blocked in the connectivity model.

### Conceptual workflow

```text
OSM Road Network
        ↓
Detect Flood-Affected Roads
        ↓
Remove / Block Affected Segments
        ↓
Run Network Connectivity Analysis
        ↓
Find Disconnected Settlements
        ↓
Identify Nearest Town/Hospital
```

---

## 5.3 Required Output

The application should show:

- Isolated settlements
- Nearest accessible town
- Nearest accessible hospital
- Previously connected road
- Current connectivity status
- Map visualization of disconnected areas

---

# 6. AI Component

The project must implement **one** of the following AI components.

---

# 6.1 Option A — Flood Segmentation Model

Train a machine-learning/deep-learning model to detect:

- Flood water
- Debris
- Flood-affected regions

The model must use the provided/listed training data.

## Required Evaluation

The model must be tested on **Himalayan scenes that were not present in the training data**.

The evaluation should demonstrate generalization to unseen Himalayan environments.

### Required outputs

- Predicted segmentation map
- Ground-truth comparison where available
- Accuracy metrics
- Visual examples
- Performance on unseen Himalayan scenes

Possible metrics:

- IoU
- Dice coefficient
- Precision
- Recall
- F1-score

---

# 6.2 Option B — Situation-Report Copilot

Build an AI assistant designed for rescue/investigation teams.

The assistant should answer questions such as:

```text
How much area was flooded?

Which roads are affected?

Which settlements are disconnected?

How many bridges are potentially affected?

Which hospital is reachable from this settlement?
```

It must also generate short situation reports in:

- English
- Nepali

---

## Critical AI Requirement — Grounded Numbers

Every numerical value generated by the copilot must come from the system's computed maps/data.

The language model must **never invent numerical values**.

### Required architecture

```text
Satellite Analysis
       ↓
Geospatial Processing
       ↓
Database / Structured Results
       ↓
AI Retrieval Layer
       ↓
Situation-Report Copilot
       ↓
English / Nepali Report
```

The LLM should generate language around verified system results rather than independently estimating numbers.

### Example

System data:

```json
{
  "flooded_area_km2": 12.4,
  "affected_roads_km": 18.7,
  "affected_bridges": 4,
  "isolated_settlements": 7
}
```

The AI may convert this into:

> "The analysis identified approximately 12.4 km² of flooded area, 18.7 km of potentially affected roads, 4 potentially affected bridges, and 7 settlements without road connectivity."

It must not replace these values with model-generated estimates.

---

# 7. Bonus Requirement — Flood Path Tracing

Given any point upstream, the system should be able to estimate the downstream flood path using elevation data.

## Workflow

```text
User Selects Upstream Point
          ↓
Elevation / DEM Data
          ↓
Determine Downstream Flow Direction
          ↓
Trace Valley / Flow Path
          ↓
Intersect With Settlements
          ↓
List Potentially Exposed Settlements
```

## Required Output

The system should display:

- Flood-path visualization
- Downstream direction
- Elevation profile where possible
- Settlements along the estimated path
- Map-based visualization

This is a **bonus feature** and is not required for the minimum viable implementation.

---

# 8. Case Study — August 2026 Trishuli Flood

The primary demonstration case must focus on the **August 2026 Trishuli flood** in Nepal.

## 8.1 Study Area

The system should define an appropriate Area of Interest covering the affected Trishuli region.

---

## 8.2 Analysis

Perform the complete workflow:

```text
Trishuli Flood Event
        ↓
Pre-Flood Sentinel-1/Sentinel-2
        +
Post-Flood Sentinel-1/Sentinel-2
        ↓
Flood / Change Detection
        ↓
Flood & Debris Map
        ↓
OSM Infrastructure Overlay
        ↓
Road Connectivity Analysis
        ↓
Affected Infrastructure
        +
Disconnected Settlements
```

---

# 9. Copernicus EMS Comparison

The results must be compared against the official Copernicus Emergency Management Service mapping products for the event.

## Reference Event

```text
Copernicus EMS Activation: EMSR927
```

The comparison should examine differences/similarities in:

- Flood extent
- Affected areas
- Infrastructure impact
- Spatial distribution
- Mapping coverage

---

## 9.1 Comparison Requirements

The system/report should clearly distinguish between:

### System Result

Generated by the project's:

- Satellite analysis
- AI/model
- OSM data
- Network analysis

### Reference Result

Official Copernicus EMS product.

---

## 9.2 Suggested Metrics

Where compatible data is available, calculate:

- Intersection over Union (IoU)
- Precision
- Recall
- Area difference
- Spatial overlap
- Infrastructure overlap

Example:

```text
                 Project Map
                     ∩
              Copernicus Map
                     ↓
              Spatial Metrics
```

---

# 10. System Architecture

The proposed system should follow a modular architecture.

```text
                    ┌──────────────────────┐
                    │    Web Application   │
                    │      Frontend        │
                    └──────────┬───────────┘
                               │
                               ▼
                    ┌──────────────────────┐
                    │      Backend API     │
                    └──────────┬───────────┘
                               │
              ┌────────────────┼────────────────┐
              │                │                │
              ▼                ▼                ▼
       Satellite Data      OSM Data       Elevation Data
              │                │                │
              ▼                ▼                ▼
       Image Processing   Infrastructure    Flow Analysis
              │             Analysis             │
              └───────────────┼──────────────────┘
                              ▼
                    ┌──────────────────────┐
                    │ Geospatial Analysis  │
                    └──────────┬───────────┘
                               │
                ┌──────────────┴──────────────┐
                ▼                             ▼
        Flood/Impact Maps              Road Connectivity
                │                             │
                └──────────────┬──────────────┘
                               ▼
                    ┌──────────────────────┐
                    │     AI Component     │
                    │ Segmentation /       │
                    │ Situation Copilot    │
                    └──────────┬───────────┘
                               ▼
                    ┌──────────────────────┐
                    │ Rescue Intelligence  │
                    │ Dashboard & Reports  │
                    └──────────────────────┘
```

---

# 11. Core Data Sources

The implementation should make use of appropriate open/public geospatial datasets.

| Data | Purpose |
|---|---|
| Sentinel-1 | Flood detection under cloudy/monsoon conditions |
| Sentinel-2 | Optical imagery and visual/change analysis |
| OpenStreetMap | Roads, buildings, bridges and settlements |
| DEM/Elevation Data | Bonus flood-path/downstream analysis |
| Copernicus EMS EMSR927 | Case-study reference/comparison |

---

# 12. Functional Requirements

The application must provide the following functionality:

### FR-01 — AOI Selection

Users can define/select an area of interest.

### FR-02 — Flood Date

Users can provide the flood/event date.

### FR-03 — Satellite Acquisition

The system identifies suitable pre- and post-event satellite imagery.

### FR-04 — Flood Detection

The system generates a flood-affected-area map.

### FR-05 — Debris/Change Detection

The system identifies relevant post-event surface changes/debris where supported by the selected methodology.

### FR-06 — Infrastructure Overlay

The system overlays OSM infrastructure on the impact map.

### FR-07 — Infrastructure Impact Estimation

The system estimates potentially affected buildings, roads and bridges.

### FR-08 — Road Connectivity

The system analyzes road-network connectivity.

### FR-09 — Isolated Settlement Detection

The system identifies settlements disconnected from relevant towns/hospitals.

### FR-10 — AI Component

The system implements either:

- Flood segmentation model, **or**
- Evidence-grounded situation-report copilot.

### FR-11 — Case Study

The system demonstrates the August 2026 Trishuli flood.

### FR-12 — EMS Comparison

The results are compared with Copernicus EMS activation EMSR927.

### FR-13 — Interactive Visualization

All major results should be viewable through an interactive map/dashboard.

---

# 13. Non-Functional Requirements

## Accuracy

The system should clearly report uncertainty and distinguish estimates from confirmed damage.

## Reproducibility

The same inputs and processing configuration should produce reproducible results where the underlying data is unchanged.

## Explainability

Users should be able to understand how:

- Flood areas were detected
- Infrastructure was classified as potentially affected
- Settlements were determined to be disconnected

## Performance

The application should process a practical AOI within a reasonable time for demonstration.

## Usability

The interface should be designed for:

- Rescue teams
- Disaster-management personnel
- GIS analysts
- Technical evaluators

The dashboard should prioritize maps and actionable spatial information rather than unnecessary visual complexity.

---

# 14. Expected Application Output

For a selected flood event, the system should produce a unified operational view containing:

```text
┌──────────────────────────────────────────┐
│          FLOOD RESCUE DASHBOARD          │
├──────────────────────────────────────────┤
│                                          │
│  Flood Extent Map                        │
│                                          │
│  ┌────────────────────────────────────┐  │
│  │                                    │  │
│  │       Interactive Map              │  │
│  │                                    │  │
│  │  Flood • Debris • Roads •          │  │
│  │  Buildings • Bridges • Settlements │  │
│  │                                    │  │
│  └────────────────────────────────────┘  │
│                                          │
│  Flooded Area       XX km²               │
│  Roads Affected     XX km                │
│  Bridges Affected   XX                   │
│  Buildings Affected XX                   │
│  Cut-off Settlements XX                  │
│                                          │
│  Connectivity Analysis                   │
│  Situation Report                        │
│  AI Assistant                            │
│                                          │
└──────────────────────────────────────────┘
```

All displayed numerical values should originate from the underlying geospatial analysis.

---

# 15. Minimum Viable Product

The minimum viable implementation must support:

1. AOI selection
2. Flood-date input
3. Sentinel-1/Sentinel-2 pre/post-event analysis
4. Flood-affected-area mapping
5. OSM infrastructure overlay
6. Affected road/building/bridge estimation
7. Road connectivity analysis
8. Identification of disconnected settlements
9. One AI component
10. Trishuli flood case study
11. Comparison with EMSR927 reference products

---

# 16. Recommended Demonstration Flow

During the final demonstration:

### Step 1 — Select Event

```text
Trishuli, Nepal
August 2026
```

### Step 2 — Load Satellite Data

Display:

```text
Before Flood
        ↓
After Flood
```

### Step 3 — Generate Flood Map

Show detected:

- Flooded areas
- Change/debris areas

### Step 4 — Add Infrastructure

Toggle:

- Roads
- Buildings
- Bridges
- Settlements

### Step 5 — Calculate Impact

Display system-derived statistics.

### Step 6 — Analyze Connectivity

Show:

```text
Connected Settlements
        vs.
Disconnected Settlements
```

### Step 7 — Use AI Component

Demonstrate either:

- Segmentation results, or
- Evidence-grounded rescue assistant

### Step 8 — Validate

Compare the generated results against:

```text
Copernicus EMS
EMSR927
```

### Step 9 — Optional Bonus

Select an upstream point and demonstrate downstream flood-path tracing.

---

# 17. Key Design Principle

The system should function as a **geospatial decision-support tool for rescue teams**, not merely as a satellite-image visualization application.

The final product should connect:

```text
Satellite Evidence
        ↓
Flood / Change Detection
        ↓
Infrastructure Impact
        ↓
Road Connectivity
        ↓
Affected / Isolated Settlements
        ↓
Actionable Rescue Intelligence
```

The AI layer must assist with interpretation and communication while keeping factual measurements grounded in the system's underlying geospatial data.
