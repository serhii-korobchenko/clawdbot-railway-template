#!/usr/bin/env python3
"""Strict deterministic parser for PROROK_REFRESH_DRY_RUN reports."""

from __future__ import annotations
from dataclasses import dataclass
from typing import Any

PARSER_VERSION = "2"
HEADER="PROROK_REFRESH_DRY_RUN"; CANDIDATE_SECTION="CANDIDATE_EVIDENCE:"; ASSESSMENT_SECTION="ASSESSMENT_RECOMMENDATION:"; DB_ACTION_SECTION="DB_ACTION:"; NO_EVIDENCE="NO_NEW_EVIDENCE_FOUND"
ALLOWED_DIRECTIONS={"indicator","counterindicator"}; ALLOWED_STRENGTHS={"weak","medium","strong"}; ALLOWED_CONFIDENCE={"low","medium","high"}; ALLOWED_DUPLICATE_RISK={"low","medium","high"}; ALLOWED_FRESHNESS={"new_after_last_assessment","missed_baseline_evidence"}
PROBABILITY_SCALE=((0,5,"0-5%","Віддалена можливість"),(10,20,"10-20%","Ймовірність низька"),(25,35,"25-35%","Малоймовірно"),(40,50,"40-50%","Реалістична можливість"),(55,75,"55-75%","Ймовірно"),(80,90,"80-90%","Висока ймовірність"),(95,100,"95-100%","Майже напевно"))
ALLOWED_PROBABILITIES={n for low,high,_,_ in PROBABILITY_SCALE for n in range(low,high+1,5)}
HEADER_KEYS=("event_id","baseline_probability","search_window")
CANDIDATE_KEYS=("direction","strength","relevance","credibility","title","source","url","published_at","summary","why_it_matters","duplicate_risk","freshness")
ASSESSMENT_KEYS=("recommended_probability","recommended_band","recommended_label","confidence","change_from_baseline","probability_delta","net_evidence_direction","net_evidence_impact","baseline_incorporation","category_transition","rationale","delta_justification")
DB_ACTION_KEYS=("do_not_write","next_step")

class RefreshParseError(ValueError): pass

@dataclass(frozen=True)
class RefreshCandidate:
    ordinal:int; direction:str; strength:str; relevance:int; credibility:int; title:str; source:str; url:str; published_at:str; summary:str; why_it_matters:str; duplicate_risk:str; freshness:str
    def as_dict(self)->dict[str,Any]: return self.__dict__.copy()

@dataclass(frozen=True)
class RefreshParseResult:
    event_id:str; baseline_probability:int; search_window:str; outcome:str; no_evidence_reason:str|None; candidates:tuple[RefreshCandidate,...]; recommended_probability:int|None; recommended_band:str|None; recommended_label:str|None; recommendation_confidence:str; change_from_baseline:str; probability_delta:int|None; net_evidence_direction:str|None; net_evidence_impact:str|None; baseline_incorporation:str|None; category_transition:bool|None; recommendation_reason:str; delta_justification:str|None; do_not_write:bool; next_step:str
    @property
    def new_evidence_count(self): return len(self.candidates)
    @property
    def indicator_count(self): return sum(x.direction=="indicator" for x in self.candidates)
    @property
    def counterindicator_count(self): return sum(x.direction=="counterindicator" for x in self.candidates)
    @property
    def change_recommended(self): return self.change_from_baseline in {"increase","decrease"}
    def event_result_fields(self):
        return {"outcome":self.outcome,"search_window":self.search_window,"no_evidence_reason":self.no_evidence_reason,"new_evidence_count":self.new_evidence_count,"indicator_count":self.indicator_count,"counterindicator_count":self.counterindicator_count,"recommended_probability":self.recommended_probability,"recommended_band":self.recommended_band,"recommended_label":self.recommended_label,"recommendation_confidence":self.recommendation_confidence,"change_from_baseline":self.change_from_baseline,"change_recommended":int(self.change_recommended),"probability_delta":self.probability_delta,"net_evidence_direction":self.net_evidence_direction,"net_evidence_impact":self.net_evidence_impact,"baseline_incorporation":self.baseline_incorporation,"category_transition":None if self.category_transition is None else int(self.category_transition),"recommendation_reason":self.recommendation_reason,"delta_justification":self.delta_justification,"do_not_write":int(self.do_not_write),"next_step":self.next_step}

def _decode_report(report):
    if isinstance(report,bytes):
        try:return report.decode("utf-8",errors="strict")
        except UnicodeDecodeError as exc: raise RefreshParseError(f"report is not valid UTF-8: {exc}") from exc
    if not isinstance(report,str): raise RefreshParseError("report must be str or UTF-8 bytes")
    return report
def _nonempty(v,f):
    v=v.strip()
    if not v: raise RefreshParseError(f"{f} must not be empty")
    return v
def _parse_percent(v,f,allow_na=False):
    raw=v.strip()
    if allow_na and raw.lower()=="n/a": return None
    if raw.endswith("%"): raw=raw[:-1].strip()
    try:n=int(raw)
    except ValueError as exc: raise RefreshParseError(f"{f} must be an integer percent or n/a") from exc
    if not 0<=n<=100: raise RefreshParseError(f"{f} must be between 0 and 100")
    return n
def _parse_int(v,f,allow_na=False):
    raw=v.strip()
    if allow_na and raw.lower()=="n/a": return None
    try:return int(raw)
    except ValueError as exc: raise RefreshParseError(f"{f} must be an integer or n/a") from exc
def _parse_score(v,f):
    n=_parse_int(v,f)
    if not 0<=n<=100: raise RefreshParseError(f"{f} must be between 0 and 100")
    return n
def _split(line):
    if ":" not in line:return None
    k,v=line.split(":",1); k=k.strip()
    if not k or any(c.isspace() for c in k):return None
    return k,v.strip()
def _block(lines,keys,name):
    vals={}; last=None
    for raw in lines:
        line=raw.strip()
        if not line:continue
        p=_split(line)
        if p and p[0] in keys:
            if p[0] in vals: raise RefreshParseError(f"duplicate key {p[0]} in {name}")
            vals[p[0]]=p[1]; last=p[0]; continue
        if p: raise RefreshParseError(f"unexpected key {p[0]} in {name}")
        if last is None: raise RefreshParseError(f"unexpected line in {name}: {line}")
        vals[last]=(vals[last]+" "+line).strip()
    missing=[k for k in keys if k not in vals]
    if missing: raise RefreshParseError(f"missing key(s) in {name}: {', '.join(missing)}")
    return vals
def _idx(lines,marker):
    a=[i for i,x in enumerate(lines) if x.strip()==marker]
    if len(a)!=1: raise RefreshParseError(f"expected exactly one {marker} section")
    return a[0]
def _scale(n):
    for low,high,band,label in PROBABILITY_SCALE:
        if low<=n<=high:return band,label
    raise RefreshParseError(f"probability {n}% is outside the defined PROROK probability scale bands")
def _parse_candidates(lines):
    m=[x for x in lines if x.strip()]
    if not m:raise RefreshParseError("CANDIDATE_EVIDENCE section is empty")
    if m[0].strip()==NO_EVIDENCE:
        b=_block(m[1:],("reason",),"CANDIDATE_EVIDENCE/no-evidence"); return (),_nonempty(b["reason"],"reason"),"no_new_evidence"
    groups=[]; ordinal=None; item=[]
    for raw in m:
        line=raw.strip()
        if line.endswith(".") and line[:-1].isdigit():
            if ordinal is not None:groups.append((ordinal,item))
            ordinal=int(line[:-1]); item=[]; continue
        if ordinal is None:raise RefreshParseError("candidate evidence must start with a numbered item such as 1.")
        item.append(raw)
    if ordinal is not None:groups.append((ordinal,item))
    if len(groups)>3:raise RefreshParseError("candidate evidence count exceeds maximum of 3")
    out=[]
    for expected,(ordinal,lines_) in enumerate(groups,1):
        if ordinal!=expected:raise RefreshParseError(f"candidate ordinals must be sequential from 1; got {ordinal}")
        d=_block(lines_,CANDIDATE_KEYS,f"CANDIDATE_EVIDENCE/{ordinal}")
        direction=d["direction"].lower(); strength=d["strength"].lower(); duplicate=d["duplicate_risk"].lower(); freshness=d["freshness"].strip()
        if direction not in ALLOWED_DIRECTIONS:raise RefreshParseError("direction must be indicator or counterindicator")
        if strength not in ALLOWED_STRENGTHS:raise RefreshParseError(f"invalid strength: {d['strength']!r}")
        if duplicate not in ALLOWED_DUPLICATE_RISK:raise RefreshParseError(f"invalid duplicate_risk: {d['duplicate_risk']!r}")
        if freshness not in ALLOWED_FRESHNESS:raise RefreshParseError(f"invalid freshness: {freshness!r}")
        out.append(RefreshCandidate(ordinal,direction,strength,_parse_score(d["relevance"],"relevance"),_parse_score(d["credibility"],"credibility"),_nonempty(d["title"],"title"),_nonempty(d["source"],"source"),_nonempty(d["url"],"url"),_nonempty(d["published_at"],"published_at"),_nonempty(d["summary"],"summary"),_nonempty(d["why_it_matters"],"why_it_matters"),duplicate,freshness))
    return tuple(out),None,"new_evidence"

def parse_refresh_report(report,*,expected_event_id=None,expected_baseline_probability=None):
    lines=_decode_report(report).replace("\r\n","\n").replace("\r","\n").split("\n")
    while lines and not lines[0].strip():lines.pop(0)
    while lines and not lines[-1].strip():lines.pop()
    if not lines or lines[0].strip()!=HEADER:raise RefreshParseError(f"report must start with {HEADER}")
    ci,ai,di=_idx(lines,CANDIDATE_SECTION),_idx(lines,ASSESSMENT_SECTION),_idx(lines,DB_ACTION_SECTION)
    if not 0<ci<ai<di:raise RefreshParseError("report sections are missing or out of order")
    h=_block(lines[1:ci],HEADER_KEYS,"header"); event_id=_nonempty(h["event_id"],"event_id"); baseline=_parse_percent(h["baseline_probability"],"baseline_probability")
    if expected_event_id is not None and event_id!=expected_event_id:raise RefreshParseError(f"event_id mismatch: expected {expected_event_id!r}, got {event_id!r}")
    if expected_baseline_probability is not None and baseline!=expected_baseline_probability:raise RefreshParseError(f"baseline_probability mismatch: expected {expected_baseline_probability}, got {baseline}")
    candidates,no_reason,outcome=_parse_candidates(lines[ci+1:ai]); a=_block(lines[ai+1:di],ASSESSMENT_KEYS,"ASSESSMENT_RECOMMENDATION")
    rp=_parse_percent(a["recommended_probability"],"recommended_probability",allow_na=True); band=None if a["recommended_band"].lower()=="n/a" else a["recommended_band"].strip(); label=None if a["recommended_label"].lower()=="n/a" else _nonempty(a["recommended_label"],"recommended_label")
    conf=a["confidence"].lower()
    if conf not in ALLOWED_CONFIDENCE:raise RefreshParseError(f"invalid confidence: {a['confidence']!r}")
    change=a["change_from_baseline"].lower(); change="no_update" if change=="keep" else change
    if change not in {"increase","decrease","no_update"}:raise RefreshParseError(f"invalid change_from_baseline: {a['change_from_baseline']!r}")
    delta=_parse_int(a["probability_delta"],"probability_delta",allow_na=True)
    direction=None if a["net_evidence_direction"].lower()=="n/a" else a["net_evidence_direction"].lower()
    impact=None if a["net_evidence_impact"].lower()=="n/a" else a["net_evidence_impact"].lower()
    incorporation=None if a["baseline_incorporation"].lower()=="n/a" else a["baseline_incorporation"].lower()
    transition_raw=a["category_transition"].lower(); transition=None if transition_raw=="n/a" else {"yes":True,"no":False}.get(transition_raw)
    if transition_raw!="n/a" and transition is None:raise RefreshParseError("category_transition must be yes, no, or n/a")
    if direction is not None and direction not in {"positive","negative","balanced"}:raise RefreshParseError("invalid net_evidence_direction")
    if impact is not None and impact not in {"none","weak","moderate","strong"}:raise RefreshParseError("invalid net_evidence_impact")
    if incorporation is not None and incorporation not in {"low","medium","high"}:raise RefreshParseError("invalid baseline_incorporation")
    rationale=_nonempty(a["rationale"],"rationale"); justification=None if a["delta_justification"].lower()=="n/a" else _nonempty(a["delta_justification"],"delta_justification")
    db=_block(lines[di+1:],DB_ACTION_KEYS,"DB_ACTION")
    if db["do_not_write"].lower()!="true":raise RefreshParseError("do_not_write must be true")
    if outcome=="no_new_evidence":
        if any(x is not None for x in (rp,band,label,delta,direction,impact,incorporation,transition,justification)):raise RefreshParseError("NO_NEW_EVIDENCE_FOUND requires recommendation/calibration fields: n/a")
        if change!="no_update":raise RefreshParseError("NO_NEW_EVIDENCE_FOUND requires change_from_baseline: no_update")
    else:
        if rp is None:raise RefreshParseError("candidate evidence requires numeric recommended_probability")
        if rp not in ALLOWED_PROBABILITIES:raise RefreshParseError(f"recommended_probability {rp}% is not an allowed PROROK probability value")
        expected_band,expected_label=_scale(rp)
        if band!=expected_band:raise RefreshParseError(f"recommended_band does not match recommended_probability; expected {expected_band}")
        if label!=expected_label:raise RefreshParseError(f"recommended_label does not match recommended_probability; expected {expected_label}")
        expected_delta=rp-baseline
        if delta!=expected_delta:raise RefreshParseError(f"probability_delta must equal recommended_probability - baseline_probability ({expected_delta})")
        expected_change="increase" if expected_delta>0 else "decrease" if expected_delta<0 else "no_update"
        if change!=expected_change:raise RefreshParseError(f"change_from_baseline must be {expected_change}")
        baseline_band,_=_scale(baseline); expected_transition=band!=baseline_band
        if transition!=expected_transition:raise RefreshParseError(f"category_transition must be {'yes' if expected_transition else 'no'}")
        if direction is None or impact is None or incorporation is None or justification is None:raise RefreshParseError("candidate evidence requires complete calibration fields")
    return RefreshParseResult(event_id,baseline,_nonempty(h["search_window"],"search_window"),outcome,no_reason,candidates,rp,band,label,conf,change,delta,direction,impact,incorporation,transition,rationale,justification,True,_nonempty(db["next_step"],"next_step"))
