"use strict";(globalThis.webpackChunk_mlflow_mlflow=globalThis.webpackChunk_mlflow_mlflow||[]).push([[3702],{33702(e,n,t){t.d(n,{r:()=>te});var a=t(68248),o=t(76118),i=t(27757),r=t(82716),s=t(78171),l=t(11522),d=t(40724),u=t(67063),c=t(66916),m=t(46709),p=t(49620),g=t(42747),v=t(60284),h=t(91105),f=t(66221),y=t(73408);var T={name:"a41n9l",styles:"justify-content:flex-start !important"},x={name:"0",styles:""},b={name:"bcffy2",styles:"display:flex;align-items:center;justify-content:space-between"},C={name:"fhxb3m",styles:"display:flex;flex-direction:row;align-items:center"},I={name:"a41n9l",styles:"justify-content:flex-start !important"};const Y=({experimentId:e,currentRunUuid:n,setCompareToRunUuid:t,compareToRunUuid:o,setCurrentRunUuid:s})=>{const{theme:l}=(0,i.u)(),d=(0,g.tz)(),Y=(0,p.LE)(),{runInfos:S}=(0,f.Xz)(e),R=(0,m.useMemo)(()=>{if(S)return S.map(e=>{var n;return{key:e.runUuid,value:null!==(n=e.runName)&&void 0!==n?n:e.runUuid}}).filter(e=>e.key)},[S]),_=(0,m.useMemo)(()=>{if(S)return S.filter(e=>e.runUuid!==n).map(e=>{var n;return{key:e.runUuid,value:null!==(n=e.runName)&&void 0!==n?n:e.runUuid}}).filter(e=>Boolean(e.key))},[S,n]),w=null===S||void 0===S?void 0:S.find(e=>e.runUuid===n),U=null===S||void 0===S?void 0:S.find(e=>e.runUuid===o),A=(0,h.Zp)(),N=(0,h.zy)(),D=(0,m.useCallback)(n=>{const t=v.Ay.getRunPageRoute(e,n)+"/evaluations";A({pathname:t,search:N.search})},[e,N.search,A]),k=null!==s&&void 0!==s?s:D;return n?(0,y.FD)("div",{css:(0,a.AH)({display:"flex",gap:l.spacing.sm,alignItems:"center"},""),children:[(0,y.Y)("div",{css:(0,a.AH)({display:"flex",alignItems:"center",justifyContent:"flex-start",gap:l.spacing.sm},""),children:(0,y.FD)(r.DialogCombobox,{componentId:"codegen_no_dynamic_mlflow_web_js_src_experiment_tracking_components_evaluations_evaluationruncompareselector_112",id:"compare-to-run-combobox",value:n?[n]:void 0,children:[(0,y.Y)(r.DialogComboboxCustomButtonTriggerWrapper,{children:(0,y.Y)(i.B,{endIcon:(0,y.Y)(r.ChevronDownIcon,{}),componentId:"mlflow.evaluations_review.table_ui.compare_to_run_button",css:T,children:(0,y.FD)("div",{css:(0,a.AH)({display:"flex",gap:l.spacing.sm,alignItems:"center",fontSize:`${l.typography.fontSizeSm}px !important`},""),children:[(0,y.Y)(u.E,{color:Y(n)}),null!==w&&void 0!==w&&w.runName?(0,y.Y)(i.T.Hint,{children:null===w||void 0===w?void 0:w.runName}):d.formatMessage({id:"PUQxu5",defaultMessage:"Select baseline run"})]})})}),(0,y.Y)(r.DialogComboboxContent,{children:(0,y.Y)(r.DialogComboboxOptionList,{children:(R||[]).map((e,t)=>(0,y.Y)(r.DialogComboboxOptionListSelectItem,{value:e.value,onChange:n=>k(e.key),checked:e.key===n,children:(0,y.FD)("div",{css:(0,a.AH)({display:"flex",gap:l.spacing.sm,alignItems:"center"},""),children:[(0,y.Y)(u.E,{color:Y(e.key)}),e.value]})},t))})})]})}),(0,y.Y)("span",{css:x,children:d.formatMessage({id:"iYmFCZ",defaultMessage:"compare to"})}),t&&(0,y.Y)("div",{css:b,children:(0,y.FD)("div",{css:C,children:[(0,y.FD)(r.DialogCombobox,{componentId:"codegen_no_dynamic_mlflow_web_js_src_experiment_tracking_components_evaluations_evaluationruncompareselector_190",id:"compare-to-run-combobox",value:o?[o]:void 0,children:[(0,y.Y)(r.DialogComboboxCustomButtonTriggerWrapper,{children:(0,y.Y)(i.B,{endIcon:(0,y.Y)(r.ChevronDownIcon,{}),componentId:"mlflow.evaluations_review.table_ui.compare_to_run_button",css:I,children:(0,y.Y)("div",{css:(0,a.AH)({display:"flex",gap:l.spacing.sm,alignItems:"center",fontSize:`${l.typography.fontSizeSm}px !important`},""),children:null!==U&&void 0!==U&&U.runName?(0,y.FD)(y.FK,{children:[(0,y.Y)(u.E,{color:Y(o)}),(0,y.Y)(i.T.Hint,{children:null===U||void 0===U?void 0:U.runName})]}):(0,y.Y)("span",{css:(0,a.AH)({color:l.colors.textPlaceholder},""),children:d.formatMessage({id:"XkpMf+",defaultMessage:"baseline run"})})})})}),(0,y.Y)(r.DialogComboboxContent,{children:(0,y.Y)(r.DialogComboboxOptionList,{children:(_||[]).map((e,n)=>(0,y.Y)(r.DialogComboboxOptionListSelectItem,{value:e.value,onChange:n=>t(e.key),checked:e.key===o,children:(0,y.FD)("div",{css:(0,a.AH)({display:"flex",gap:l.spacing.sm,alignItems:"center"},""),children:[(0,y.Y)(u.E,{color:Y(e.key)}),e.value]})},n))})})]}),(null===U||void 0===U?void 0:U.runName)&&(0,y.Y)(c.X,{"aria-hidden":"false",css:(0,a.AH)({color:l.colors.textPlaceholder,fontSize:l.typography.fontSizeSm,marginLeft:l.spacing.sm,":hover":{color:l.colors.actionTertiaryTextHover}},""),role:"button",onClick:()=>{t(void 0)},onPointerDownCapture:e=>{e.stopPropagation()}})]})})]}):(0,y.Y)(y.FK,{})};var S=t(31655),R=t(69986),_=t(27763),w=t(54871),U=t(26765);const A=e=>(0,m.useMemo)(()=>e?(0,o.intersection)((0,U.T)(e),[f.o8.Evaluations,f.o8.Metrics,f.o8.Assessments]):[],[e]);var N=t(33656),D=t(88525),k=t(56530),E=t(5690),F=t(38232),M=t(65765),H=t(43233);const L="_assessments.json",$=()=>{const e=(0,k.wA)(),[n,t]=(0,m.useState)(!1);return{savePendingAssessments:(0,m.useCallback)(async(n,a,i)=>{try{t(!0);const r=await(async e=>{const n=(0,M.To)(L,e),t=await(0,M.Up)(n).then(e=>JSON.parse(e));if(!(0,o.isArray)(t.data)||!(0,o.isArray)(t.columns))throw new Error("Artifact is malformed and/or not valid JSON");return t})(n),s=((e,n)=>n.map(n=>{var t,a,o;return[e,n.name,{source_type:null===(t=n.source)||void 0===t?void 0:t.sourceType,source_id:null===(a=n.source)||void 0===a?void 0:a.sourceId,source_metadata:null===(o=n.source)||void 0===o?void 0:o.metadata},n.timestamp||null,n.booleanValue||null,n.numericValue||null,n.stringValue||null,n.rationale||null,n.metadata||null,null,null]}))(a,i),l=((e,n,t)=>{const a=(0,F.G4)(L,n),i=t.map(({name:e,source:n})=>({name:e,source:n?{source_type:n.sourceType,source_id:n.sourceId,source_metadata:n.metadata}:void 0})),r=a.entries.filter(({evaluation_id:n,name:t,source:a})=>e===n&&i.find(e=>(0,o.isEqual)({name:t,source:a},e))).map(e=>a.entries.indexOf(e));return n.data.filter((e,n)=>!r.includes(n))})(a,r,i);await e((0,E.Of)(n,L,{columns:r.columns,data:[...l,...s]})),e({type:(0,H.ec)(E.So),payload:(0,F.G4)(L,{columns:r.columns,data:[...l,...s]}),meta:{runUuid:n,artifactPath:L}})}catch(r){throw l.A.logErrorAndNotifyUser(r.message||r),r}finally{t(!1)}},[e]),isSaving:n}};var B=t(81641);const O=B.J1`
  query SearchRuns($data: MlflowSearchRunsInput!) {
    mlflowSearchRuns(input: $data) {
      apiError {
        helpUrl
        code
        message
      }
      runs {
        info {
          runName
          status
          runUuid
          experimentId
          artifactUri
          endTime
          lifecycleStage
          startTime
          userId
        }
        experiment {
          experimentId
          name
          tags {
            key
            value
          }
          artifactLocation
          lifecycleStage
          lastUpdateTime
        }
        data {
          metrics {
            key
            value
            step
            timestamp
          }
          params {
            key
            value
          }
          tags {
            key
            value
          }
        }
        inputs {
          datasetInputs {
            dataset {
              digest
              name
              profile
              schema
              source
              sourceType
            }
            tags {
              key
              value
            }
          }
          modelInputs {
            modelId
          }
        }
        outputs {
          modelOutputs {
            modelId
            step
          }
        }
        modelVersions {
          version
          name
          creationTimestamp
          status
          source
        }
      }
    }
  }
`,j=({filter:e,experimentIds:n,disabled:t=!1})=>{var a,i,r;const{data:s,loading:l,error:d,refetch:u}=(0,B.IT)(O,{variables:{data:{filter:e,experimentIds:n}},skip:t});return{loading:l,data:(0,o.first)(null!==(a=null===s||void 0===s||null===(i=s.mlflowSearchRuns)||void 0===i?void 0:i.runs)&&void 0!==a?a:[]),refetchRun:u,apolloError:d,apiError:null===s||void 0===s||null===(r=s.mlflowSearchRuns)||void 0===r?void 0:r.apiError}};var P={name:"r3950p",styles:"flex:1;display:flex;justify-content:center;align-items:center"};const z=({experimentId:e,runUuid:n,runTags:t,runDisplayName:o,data:l})=>{const{theme:u}=(0,i.u)(),c=A(t),m=0===(null===l||void 0===l?void 0:l.length),[p,g]=(0,s.Y)(),v=(0,N.N9)(),h=$(),{data:T,displayName:x,loading:b}=G(e,p,c);if(b)return(0,y.Y)(r.LegacySkeleton,{});const C=e=>e.filter(e=>e.type===f.$6.ASSESSMENT||e.type===f.$6.INPUT||e.type===f.$6.TRACE_INFO&&[f.tj,f.$W,f.Pn].includes(e.id));return m?(0,y.Y)("div",{css:P,children:(0,y.Y)(r.Empty,{title:(0,y.Y)(d.A,{id:"NqqMPs",defaultMessage:"No evaluation tables logged"}),description:null})}):(0,y.FD)("div",{css:(0,a.AH)({marginTop:u.spacing.sm,width:"100%",overflowY:"hidden"},""),children:[!(0,S.iA)()&&(0,y.Y)("div",{css:(0,a.AH)({width:"100%",padding:`${u.spacing.xs}px 0`},""),children:(0,y.Y)(Y,{experimentId:e,currentRunUuid:n,compareToRunUuid:p,setCompareToRunUuid:g})}),(()=>{const t={experimentId:e,currentRunDisplayName:o,currentEvaluationResults:l||[],compareToEvaluationResults:T,runUuid:n,compareToRunUuid:p,compareToRunDisplayName:x,compareToRunLoading:b,saveAssessmentsQuery:h,getTrace:R.Rb,initialSelectedColumns:C};return(0,y.Y)(f.tU,{makeHtml:v,children:(0,y.Y)(f.js,{...t})})})()]})},G=(e,n,t)=>{const{data:a,isLoading:i}=(0,f.Ie)({runUuid:n||"",artifacts:t},{disabled:(0,o.isNil)(n)}),{data:r,loading:s}=j({experimentIds:[e],filter:`attributes.run_id = "${n}"`,disabled:(0,o.isNil)(n)});return{data:a,displayName:l.A.getRunDisplayName(null===r||void 0===r?void 0:r.info,n),loading:i||s}};var q=t(39595),Q=t(82636),V=t(80747),X=t(27462),K=t(7655),W=t(55532),Z=t(25862);const J=({children:e,makeHtmlFromMarkdown:n,experimentId:t})=>(0,y.Y)(f.tU,{makeHtml:n,children:e});var ee={name:"1nxh63r",styles:"overflow-y:hidden;height:100%;display:flex;flex-direction:column"};const ne=({experimentId:e,runUuid:n,runDisplayName:t,setCurrentRunUuid:o,showCompareSelector:r=!1,showRefreshButton:l=!1,hideCompareSelector:c=!1})=>{const{theme:g}=(0,i.u)(),v=((0,K.A)(),(0,N.N9)()),[h,T]=(0,s.Y)(),[x,b]=(0,m.useState)(!1),C=(0,m.useMemo)(()=>[(0,f.$U)(e)],[e]),I=R.Uv,U=!1,{assessmentInfos:A,allColumns:k,totalCount:E,evaluatedTraces:F,otherEvaluatedTraces:M,isLoading:H,error:L,tableFilterOptions:$}=(0,f.KW)({locations:C,runUuid:n,otherRunUuid:h,disabled:U,filterByAssessmentSourceRun:!0}),[B,O]=(0,m.useState)({});(0,w.Gt)("selectedTraceIds",B);const[j,P]=(0,m.useState)(""),[z,G]=(0,f.R7)(),ne=(0,p.LE)(),te=(0,q.jE)(),ie=(0,m.useCallback)(e=>{const n=F.concat(M),{responseHasContent:t,inputHasContent:a,tokensHasContent:o}=(0,Q.l)(n),i=n.some(e=>{var n,t;return Boolean(null===(n=e.traceInfo)||void 0===n||null===(t=n.trace_metadata)||void 0===t?void 0:t[V.n2])});return e.filter(e=>e.type===f.$6.ASSESSMENT||e.type===f.$6.EXPECTATION||a&&e.type===f.$6.INPUT||t&&e.type===f.$6.TRACE_INFO&&e.id===f.Rl||o&&e.type===f.$6.TRACE_INFO&&e.id===f.YO||e.type===f.$6.TRACE_INFO&&[f.XQ,f.tj,f.$W].includes(e.id)||i&&[f.yX,f.jN,f.mq].includes(e.id))},[F,M]),{selectedColumns:re,toggleColumns:se,setSelectedColumns:le}=(0,f.K0)(e,k,ie,n),[de,ue]=(0,f.GY)(re),{data:ce,isLoading:me,isFetching:pe,error:ge,refetchMlflowTraces:ve}=(0,f.Zn)({locations:C,currentRunDisplayName:t,searchQuery:j,filters:z,runUuid:n,tableSort:de,disabled:U,filterByAssessmentSourceRun:!0}),{data:he,displayName:fe,loading:ye}=oe({experimentId:e,traceLocations:C,compareToRunUuid:h,isQueryDisabled:U}),Te=(0,m.useCallback)(()=>{b(e=>!e)},[]),xe=(0,m.useRef)(!1);(0,m.useEffect)(()=>{if(!xe.current&&ce&&ce.length>0){ce.some(e=>{var n;return Boolean(null===(n=e.trace_metadata)||void 0===n?void 0:n[V.n2])})&&b(!0),xe.current=!0}},[ce]);const be=(0,m.useMemo)(()=>({currentCount:null===ce||void 0===ce?void 0:ce.length,logCountLoading:me,totalCount:E,maxAllowedCount:(0,S.pR)()}),[ce,me,E]),{showEditTagsModalForTrace:Ce,EditTagsModal:Ie}=(0,D.$)({onSuccess:()=>(0,f.BL)({queryClient:te}),existingTagKeys:(0,f.d9)(ce||[])}),Ye=(0,X.F)({traceSearchLocations:C}),Se=W.p,Re=(0,m.useMemo)(()=>({deleteTracesAction:Ye,exportToEvals:!0,editTags:{showEditTagsModalForTrace:Ce,EditTagsModal:Ie}}),[Ye,Ce,Ie]),_e=me||ye,we=ne(n),Ue=h?ne(h):void 0,[Ae,Ne]=(0,m.useState)(!1);return H?(0,y.Y)(ae,{}):L?(0,y.Y)("div",{children:(0,y.Y)("pre",{children:String(L)})}):(0,y.FD)("div",{css:(0,a.AH)({marginTop:g.spacing.sm,width:"100%",overflowY:"hidden"},""),children:[!(0,S.iA)()&&!r&&!c&&(0,y.Y)("div",{css:(0,a.AH)({width:"100%",padding:`${g.spacing.xs}px 0`},""),children:(0,y.Y)(Y,{experimentId:e,currentRunUuid:n,compareToRunUuid:h,setCompareToRunUuid:T,setCurrentRunUuid:o})}),r&&h&&(0,y.FD)("div",{css:(0,a.AH)({display:"flex",alignItems:"center",width:"100%",paddingBottom:g.spacing.sm,gap:g.spacing.sm},""),children:[(0,y.Y)(i.T.Text,{children:(0,y.Y)(d.A,{id:"v/njcq",defaultMessage:"Comparing"})}),(0,y.FD)("span",{css:(0,a.AH)({display:"inline-flex",alignItems:"center",gap:g.spacing.xs},""),children:[we&&(0,y.Y)(u.E,{color:we}),(0,y.Y)(i.T.Text,{bold:!0,children:t})]}),(0,y.Y)(i.T.Text,{children:(0,y.Y)(d.A,{id:"taI4Bv",defaultMessage:"to"})}),(0,y.FD)("span",{css:(0,a.AH)({display:"inline-flex",alignItems:"center",gap:g.spacing.xs},""),children:[Ue&&(0,y.Y)(u.E,{color:Ue}),(0,y.Y)(i.T.Text,{bold:!0,children:fe})]})]}),(0,y.Y)(V.gm,{renderExportTracesToDatasetsModal:Se,DrawerComponent:Z.q,children:(0,y.Y)(_.Nf,{rowSelection:B,setRowSelection:O,children:(0,y.Y)(f.sG,{experimentId:e,isGroupedBySession:x,children:(0,y.FD)("div",{css:ee,children:[(0,y.Y)(f.w_,{experimentId:e,searchQuery:j,setSearchQuery:P,filters:z,setFilters:G,assessmentInfos:A,countInfo:be,traceActions:Re,tableSort:de,setTableSort:ue,allColumns:k,selectedColumns:re,setSelectedColumns:le,toggleColumns:se,traceInfos:ce,tableFilterOptions:$,onRefresh:l?ve:void 0,isRefreshing:l?pe:void 0,isGroupedBySession:x,onToggleSessionGrouping:Te}),_e?(0,y.Y)(ae,{}):ge?(0,y.Y)("div",{children:(0,y.Y)("pre",{children:String(ge)})}):(0,y.Y)(J,{makeHtmlFromMarkdown:v,experimentId:e,children:(0,y.Y)(f._p,{experimentId:e,currentRunDisplayName:t,compareToRunDisplayName:fe,runUuid:n,compareToRunUuid:h,getTrace:I,getRunColor:ne,assessmentInfos:A,setFilters:G,filters:z,selectedColumns:re,allColumns:k,tableSort:de,currentTraceInfoV3:ce||[],compareToTraceInfoV3:he,onTraceTagsEdit:Ce,isTableLoading:_e,isGroupedBySession:x})}),Ie]})})})})]})},te=({experimentId:e,experiment:n,runUuid:t,runTags:a,runDisplayName:i,setCurrentRunUuid:r,showCompareSelector:s=!1,showRefreshButton:l=!1,hideCompareSelector:d=!1})=>{const u=A(a),c=Boolean(t),{data:m,isLoading:p}=(0,f.Ie)({runUuid:t||"",artifacts:u||void 0},{disabled:!c});return p?(0,y.Y)(ae,{}):!(0,o.isNil)(m)&&m.length>0?(0,y.Y)(z,{experimentId:e,runUuid:t,runDisplayName:i,data:m,runTags:a}):(0,y.Y)(ne,{experimentId:e,runUuid:t,runDisplayName:i,setCurrentRunUuid:r,showCompareSelector:s,showRefreshButton:l,hideCompareSelector:d})},ae=()=>{const{theme:e}=(0,i.u)();return(0,y.Y)("div",{css:(0,a.AH)({display:"block",marginTop:e.spacing.md,height:"100%",width:"100%"},""),children:[...Array(10).keys()].map(e=>(0,y.Y)(r.ParagraphSkeleton,{label:"Loading...",seed:`s-${e}`},e))})},oe=e=>{const{compareToRunUuid:n,experimentId:t,traceLocations:a,isQueryDisabled:i}=e,{data:r,isLoading:s}=(0,f.Zn)({locations:a,currentRunDisplayName:void 0,runUuid:n,disabled:(0,o.isNil)(n)||i,filterByAssessmentSourceRun:!0}),{data:d,loading:u}=j({experimentIds:[t],filter:`attributes.run_id = "${n}"`,disabled:(0,o.isNil)(n)});return{data:r,displayName:l.A.getRunDisplayName(null===d||void 0===d?void 0:d.info,n),loading:s||u}}},78171(e,n,t){t.d(n,{V:()=>i,Y:()=>r});var a=t(46709),o=t(91105);const i="compareToRunUuid",r=()=>{var e;const[n,t]=(0,o.ok)();return[null!==(e=n.get(i))&&void 0!==e?e:void 0,(0,a.useCallback)(e=>{t(n=>void 0===e?(n.delete(i),n):(n.set(i,e),n))},[t])]}}}]);