"use strict";(globalThis.webpackChunk_mlflow_mlflow=globalThis.webpackChunk_mlflow_mlflow||[]).push([[2584],{97587(e,r,t){t.d(r,{f:()=>u});var a=t(68248),o=t(82716),n=t(27757),l=t(46709),i=t(75173),s=t(29774),d=t(73408);const u=({datasetName:e,datasetDigest:r,runId:t})=>{const{theme:u}=(0,n.u)(),[c,v]=(0,l.useState)(!1),{onDatasetClicked:m}=(0,i.s7)(),{handleError:p}=(0,s.tF)();return(0,d.FD)(n.B,{type:"link",icon:c?(0,d.Y)(n.S,{size:"small",css:(0,a.AH)({marginRight:u.spacing.sm},"")}):(0,d.Y)(o.TableIcon,{}),componentId:"mlflow.logged_model.dataset",onClick:()=>((e,r,t)=>{t&&(v(!0),null===m||void 0===m||m({datasetName:e,datasetDigest:r,runId:t}).catch(e=>{p(e)}).finally(()=>v(!1)))})(e,r,t),children:[e," (#",r,")"]},[e,r].join("."))}},79557(e,r,t){t.d(r,{J:()=>h});var a=t(68248),o=t(82716),n=t(27757),l=t(79081),i=t(11522),s=t(44105),d=t(91701),u=t(42762),c=t(46709),v=t(91105),m=t(73408);var p={name:"1wcfv52",styles:"margin-right:0"},g={name:"1a7v7i3",styles:"margin-right:0;&>div{padding-right:0;}"};const h=({loggedModel:e,displayDetails:r,className:t})=>{var h,f,E,y;const[D]=(0,v.ok)(),k=(0,c.useMemo)(()=>{var r,t,a;return null!==(r=null===e||void 0===e||null===(t=e.info)||void 0===t||null===(a=t.tags)||void 0===a?void 0:a.reduce((e,r)=>r.key?(e[r.key]=r,e):e,{}))&&void 0!==r?r:{}},[null===e||void 0===e||null===(h=e.info)||void 0===h?void 0:h.tags]),I=null===k||void 0===k||null===(f=k[s.xd])||void 0===f?void 0:f.value,_=null===k||void 0===k||null===(E=k[i.A.gitCommitTag])||void 0===E?void 0:E.value,w=(0,c.useMemo)(()=>{try{return i.A.renderSource(k,D.toString(),void 0,I)}catch(e){return}},[k,D,I]),M=null===(y=k[i.A.sourceTypeTag])||void 0===y?void 0:y.value,{theme:A}=(0,n.u)();return w?(0,m.FD)("div",{css:(0,a.AH)({display:"flex",alignItems:"center",gap:A.spacing.sm,paddingTop:A.spacing.sm,paddingBottom:A.spacing.sm,flexWrap:r?"wrap":void 0},""),className:t,children:[M&&(0,m.Y)(u.m,{sourceType:M,css:(0,a.AH)({color:A.colors.actionPrimaryBackgroundDefault},"")}),w," ",r&&I&&(0,m.Y)(l.T,{componentId:"mlflow.logged_model.details.source.branch_tooltip",content:I,children:(0,m.Y)(o.Tag,{componentId:"mlflow.logged_model.details.source.branch",css:p,children:(0,m.FD)("div",{css:(0,a.AH)({display:"flex",gap:A.spacing.xs,whiteSpace:"nowrap"},""),children:[(0,m.Y)(o.BranchIcon,{})," ",I]})})}),r&&_&&(0,m.FD)(n.aw.Root,{componentId:"mlflow.logged_model.details.source.commit_hash_popover",children:[(0,m.Y)(n.aw.Trigger,{asChild:!0,children:(0,m.Y)(o.Tag,{componentId:"mlflow.logged_model.details.source.commit_hash",css:g,children:(0,m.FD)("div",{css:(0,a.AH)({display:"flex",gap:A.spacing.xs,whiteSpace:"nowrap",alignContent:"center"},""),children:[(0,m.Y)(o.GitCommitIcon,{}),_.slice(0,7)]})})}),(0,m.FD)(n.aw.Content,{align:"start",children:[(0,m.Y)(n.aw.Arrow,{}),(0,m.FD)("div",{css:(0,a.AH)({display:"flex",gap:A.spacing.xs,alignItems:"center"},""),children:[_,(0,m.Y)(d.i,{showLabel:!1,size:"small",type:"tertiary",copyText:_,icon:(0,m.Y)(o.CopyIcon,{})})]})]})]})]}):(0,m.Y)(n.T.Hint,{children:"\u2014"})}},86134(e,r,t){t.d(r,{a:()=>c});var a=t(68248),o=t(82716),n=t(27757),l=t(66916),i=t(40724),s=t(67069),d=t(73408);const u=({status:e})=>e===s.Fq.LOGGED_MODEL_READY?(0,d.Y)(o.CheckCircleIcon,{color:"success"}):e===s.Fq.LOGGED_MODEL_UPLOAD_FAILED?(0,d.Y)(o.XCircleIcon,{color:"danger"}):e===s.Fq.LOGGED_MODEL_PENDING?(0,d.Y)(l.C,{color:"warning"}):null,c=({data:e})=>{var r,t;const{theme:l}=(0,n.u)(),c=null!==(r=null===(t=e.info)||void 0===t?void 0:t.status)&&void 0!==r?r:s.Fq.LOGGED_MODEL_STATUS_UNSPECIFIED;return c===s.Fq.LOGGED_MODEL_STATUS_UNSPECIFIED?null:(0,d.FD)(o.Tag,{componentId:"mlflow.logged_model.status",css:(0,a.AH)({backgroundColor:c===s.Fq.LOGGED_MODEL_READY?l.isDarkMode?l.colors.green800:l.colors.green100:c===s.Fq.LOGGED_MODEL_UPLOAD_FAILED?l.isDarkMode?l.colors.red800:l.colors.red100:c===s.Fq.LOGGED_MODEL_PENDING?l.isDarkMode?l.colors.yellow800:l.colors.yellow100:void 0},""),children:[c&&(0,d.Y)(u,{status:c})," ",(0,d.Y)(n.T.Text,{css:(0,a.AH)({marginLeft:l.spacing.sm},""),children:c===s.Fq.LOGGED_MODEL_READY?(0,d.Y)(n.T.Text,{color:"success",children:(0,d.Y)(i.A,{id:"Rs+SVS",defaultMessage:"Ready"})}):c===s.Fq.LOGGED_MODEL_UPLOAD_FAILED?(0,d.Y)(n.T.Text,{color:"error",children:(0,d.Y)(i.A,{id:"e6reUn",defaultMessage:"Failed"})}):c===s.Fq.LOGGED_MODEL_PENDING?(0,d.Y)(n.T.Text,{color:"warning",children:(0,d.Y)(i.A,{id:"jo4LfR",defaultMessage:"Pending"})}):c})]})}},17763(e,r,t){t.d(r,{P:()=>n});t(46709);var a=t(16144),o=t(73408);const n=({value:e})=>{const r=new Date(Number(e));return isNaN(r)?null:(0,o.Y)(a.f,{date:r})}},75173(e,r,t){t.d(r,{Xs:()=>p,s7:()=>g});var a=t(46709),o=t(48292),n=t(99002),l=t(19816),i=t(76118),s=t(99209),d=t(24523),u=t(40724),c=t(73408);class v extends s.ZR{constructor(...e){super(...e),this.errorLogType=s.ZQ.UnexpectedSystemStateError,this.errorName=s.UW.DatasetRunNotFoundError,this.isUserError=!0,this.displayMessage=(0,c.Y)(u.A,{id:"vwDBPr",defaultMessage:"The run containing the dataset could not be found."})}}const m=(0,a.createContext)({onDatasetClicked:()=>Promise.resolve()}),p=({children:e})=>{const[r,t]=(0,a.useState)(!1),[s,u]=(0,a.useState)(),[p]=(0,n.T)(),g=(0,a.useRef)(null),h=(0,a.useCallback)(async e=>new Promise((r,a)=>{var o;return null===(o=g.current)||void 0===o||o.call(g),p({onError:a,onCompleted(o){var n,s,c,m,p,h,f,E,y,D,k;if(null!==(n=o.mlflowGetRun)&&void 0!==n&&n.apiError){const e=o.mlflowGetRun.apiError.code===d.tG.RESOURCE_DOES_NOT_EXIST?new v:o.mlflowGetRun.apiError;return void a(e)}const I=(0,l.u)(null===(s=o.mlflowGetRun)||void 0===s||null===(c=s.run)||void 0===c||null===(m=c.inputs)||void 0===m?void 0:m.datasetInputs);if(!I||null===(p=o.mlflowGetRun)||void 0===p||null===(h=p.run)||void 0===h||!h.info)return void r();const _=null===I||void 0===I?void 0:I.find(r=>{var t;return(null===(t=r.dataset)||void 0===t?void 0:t.digest)===e.datasetDigest&&r.dataset.name===e.datasetName});if(!_)return void r();const{info:w,data:M}=o.mlflowGetRun.run,A=(0,i.keyBy)(null!==(f=null===M||void 0===M||null===(E=M.tags)||void 0===E?void 0:E.filter(e=>e.key&&e.value))&&void 0!==f?f:[],"key");t(!0),u({datasetWithTags:{dataset:_.dataset,tags:_.tags},runData:{datasets:I,runUuid:null!==(y=w.runUuid)&&void 0!==y?y:"",experimentId:null!==(D=w.experimentId)&&void 0!==D?D:"",runName:null!==(k=w.runName)&&void 0!==k?k:"",tags:A}}),r(),g.current=null},variables:{data:{runId:e.runId}}})}),[p]),f=(0,a.useMemo)(()=>({onDatasetClicked:h}),[h]);return(0,c.FD)(m.Provider,{value:f,children:[e,s&&(0,c.Y)(o.O,{isOpen:r,selectedDatasetWithRun:s,setIsOpen:t,setSelectedDatasetWithRun:u})]})},g=()=>(0,a.useContext)(m)},99002(e,r,t){t.d(r,{T:()=>l,t:()=>n});var a=t(81641);const o=a.J1`
  query GetRun($data: MlflowGetRunInput!) @component(name: "MLflow.ExperimentRunTracking") {
    mlflowGetRun(input: $data) {
      apiError {
        helpUrl
        code
        message
      }
      run {
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
        modelVersions {
          status
          version
          name
          source
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
      }
    }
  }
`,n=({runUuid:e,disabled:r=!1})=>{var t,n;const{data:l,loading:i,error:s,refetch:d}=(0,a.IT)(o,{variables:{data:{runId:e}},skip:r});return{loading:i,data:null===l||void 0===l||null===(t=l.mlflowGetRun)||void 0===t?void 0:t.run,refetchRun:d,apolloError:s,apiError:null===l||void 0===l||null===(n=l.mlflowGetRun)||void 0===n?void 0:n.apiError}},l=()=>(0,a._l)(o)},19816(e,r,t){t.d(r,{u:()=>p,g:()=>g});var a=t(76118),o=t(46709),n=t(56530),l=t(5690),i=t(99213),s=t(11522);var d=t(99002),u=t(31655),c=t(27206);const v=({queryResult:e,runUuid:r})=>{const{registeredModels:t}=(0,n.d4)(({entities:e})=>({registeredModels:e.modelVersionsByRunUuid[r]}));if((0,u._O)()){const r=[];var a,o;if(null!==e&&void 0!==e&&e.data&&"modelVersions"in e.data)null===(a=e.data)||void 0===a||null===(o=a.modelVersions)||void 0===o||o.forEach(e=>{r.push({displayedName:e.name,version:e.version,link:e.name&&e.version?c.fM.getModelVersionPageRoute(e.name,e.version):"",status:e.status,source:e.source})});return r}return t?t.map(e=>{const r=e.name,t=c.fM.getModelVersionPageRoute(r,e.version);return{displayedName:e.name,version:e.version,link:t,status:e.status,source:e.source}}):[]},m=e=>(0,a.keyBy)(e,"key"),p=e=>null===e||void 0===e?void 0:e.map(e=>{var r,t,o,n,l,i,s,d,u,c,v,m,p,g;return{dataset:{digest:null!==(r=null===(t=e.dataset)||void 0===t?void 0:t.digest)&&void 0!==r?r:"",name:null!==(o=null===(n=e.dataset)||void 0===n?void 0:n.name)&&void 0!==o?o:"",profile:null!==(l=null===(i=e.dataset)||void 0===i?void 0:i.profile)&&void 0!==l?l:"",schema:null!==(s=null===(d=e.dataset)||void 0===d?void 0:d.schema)&&void 0!==s?s:"",source:null!==(u=null===(c=e.dataset)||void 0===c?void 0:c.source)&&void 0!==u?u:"",sourceType:null!==(v=null===(m=e.dataset)||void 0===m?void 0:m.sourceType)&&void 0!==v?v:""},tags:null!==(p=null===(g=e.tags)||void 0===g?void 0:g.map(e=>{var r,t;return{key:null!==(r=e.key)&&void 0!==r?r:"",value:null!==(t=e.value)&&void 0!==t?t:""}}).filter(e=>!(0,a.isEmpty)(e.key)))&&void 0!==p?p:[]}}),g=({runUuid:e,experimentId:r})=>{var t,c,g,h,f,E;const y=(0,u.wD)(),D=(0,n.wA)(),k=!0;if(y){var I,_,w,M,A,T;const r=(()=>(0,d.t)({runUuid:e}))();(0,o.useEffect)(()=>{(0,u._O)()||D((0,i.hY)({run_id:e}))},[D,e,k]);const{latestMetrics:t,tags:n,params:l,datasets:s}=(0,o.useMemo)(()=>{var e,t,o,n,l,i,s,d,u,c,v,g;return{latestMetrics:(0,a.pickBy)(m((g=null!==(e=null===(t=r.data)||void 0===t||null===(o=t.data)||void 0===o?void 0:o.metrics)&&void 0!==e?e:[],g.filter(({key:e,value:r,step:t,timestamp:a})=>null!==e&&null!==r&&null!==t&&null!==a).map(({key:e,value:r,step:t,timestamp:a})=>({key:e,value:r,step:Number(t),timestamp:Number(a)})))),e=>e.key.trim().length>0),tags:(0,a.pickBy)(m(null!==(n=null===(l=r.data)||void 0===l||null===(i=l.data)||void 0===i?void 0:i.tags)&&void 0!==n?n:[]),e=>e.key.trim().length>0),params:(0,a.pickBy)(m(null!==(s=null===(d=r.data)||void 0===d||null===(u=d.data)||void 0===u?void 0:u.params)&&void 0!==s?s:[]),e=>e.key.trim().length>0),datasets:p(null===(c=r.data)||void 0===c||null===(v=c.inputs)||void 0===v?void 0:v.datasetInputs)}},[r.data]),c=v({runUuid:e,queryResult:r});return{runInfo:null!==(I=null===(_=r.data)||void 0===_?void 0:_.info)&&void 0!==I?I:void 0,experiment:null!==(w=null===(M=r.data)||void 0===M?void 0:M.experiment)&&void 0!==w?w:void 0,loading:r.loading,error:r.apolloError,apiError:r.apiError,refetchRun:r.refetchRun,runInputs:null===(A=r.data)||void 0===A?void 0:A.inputs,runOutputs:null===(T=r.data)||void 0===T?void 0:T.outputs,registeredModelVersionSummaries:c,datasets:s,latestMetrics:t,tags:n,params:l}}const R=((e,r,t=!0)=>{const[d,u]=(0,o.useState)(""),[c,v]=(0,o.useState)(""),m=(0,n.wA)(),{runInfo:p,tags:g,latestMetrics:h,experiment:f,params:E,datasets:y}=(0,n.d4)(t=>({runInfo:t.entities.runInfosByUuid[e],tags:(0,a.pickBy)(t.entities.tagsByRunUuid[e],e=>e.key.trim().length>0),latestMetrics:(0,a.pickBy)(t.entities.latestMetricsByRunUuid[e],e=>e.key.trim().length>0),params:(0,a.pickBy)(t.entities.paramsByRunUuid[e],e=>e.key.trim().length>0),experiment:t.entities.experimentsById[r],datasets:t.entities.runDatasetsByUuid[e]})),D=(0,o.useCallback)(()=>{const r=(0,l.aO)(e);return u(r.meta.id),m(r)},[m,e]),k=(0,o.useCallback)(()=>{const e=(0,l.yc)(r);return v(e.meta.id),m(e)},[m,r]),I=(0,o.useCallback)(()=>{t&&m((0,i.hY)({run_id:e}))},[m,e,t]);(0,o.useEffect)(()=>{p||D().catch(e=>s.A.logErrorAndNotifyUser(e)),I()},[p,D,I]),(0,o.useEffect)(()=>{f||k().catch(e=>s.A.logErrorAndNotifyUser(e))},[f,k]);const{loading:_,error:w}=(0,n.d4)(e=>{var r,t,a,o;return{loading:!d||Boolean(null===(r=e.apis)||void 0===r||null===(t=r[d])||void 0===t?void 0:t.active),error:null===(a=e.apis)||void 0===a||null===(o=a[d])||void 0===o?void 0:o.error}}),{loading:M,error:A}=(0,n.d4)(e=>{var r,t,a,o;return{loading:!d||Boolean(null===(r=e.apis)||void 0===r||null===(t=r[c])||void 0===t?void 0:t.active),error:null===(a=e.apis)||void 0===a||null===(o=a[c])||void 0===o?void 0:o.error}});return{loading:_||M,data:{runInfo:p,tags:g,params:E,latestMetrics:h,experiment:f,datasets:y},refetchRun:D,errors:{runFetchError:w,experimentFetchError:A}}})(e,r,k),U=R.errors.runFetchError||R.errors.experimentFetchError,x=v({runUuid:e});return{runInfo:null===(t=R.data)||void 0===t?void 0:t.runInfo,latestMetrics:null===(c=R.data)||void 0===c?void 0:c.latestMetrics,tags:null===(g=R.data)||void 0===g?void 0:g.tags,experiment:null===(h=R.data)||void 0===h?void 0:h.experiment,params:null===(f=R.data)||void 0===f?void 0:f.params,datasets:null===(E=R.data)||void 0===E?void 0:E.datasets,loading:R.loading,error:U,runFetchError:R.errors.runFetchError,experimentFetchError:R.errors.experimentFetchError,refetchRun:R.refetchRun,registeredModelVersionSummaries:x}}},16144(e,r,t){t.d(r,{f:()=>a.f});var a=t(64226)},29774(e,r,t){t.d(r,{Au:()=>i,tF:()=>s});var a=t(46709),o=t(99209),n=t(73408);const l=(0,a.createContext)({currentUserActionError:null,handleError:()=>{},handlePromise:()=>{},clearUserActionError:()=>{}}),i=({children:e,errorFilter:r})=>{const[t,i]=(0,a.useState)(null),s=(0,a.useCallback)((e,t)=>{if(null===r||void 0===r||!r(e)){const r=(0,o.a$)(e);i(r),t&&t(r)}},[i,r]),d=(0,a.useCallback)(e=>{e.catch(e=>{s(e)})},[s]),u=(0,a.useCallback)(()=>{i(null)},[i]);return(0,n.Y)(l.Provider,{value:(0,a.useMemo)(()=>({currentUserActionError:t,handleError:s,handlePromise:d,clearUserActionError:u}),[u,t,s,d]),children:e})},s=()=>{const{currentUserActionError:e,handleError:r,handlePromise:t,clearUserActionError:o}=(0,a.useContext)(l),n=(0,a.useCallback)((e,t,a)=>{r(t,a)},[r]);return(0,a.useMemo)(()=>({currentUserActionError:e,handleError:r,handleErrorWithEvent:n,handlePromise:t,clearUserActionError:o}),[o,r,t,e,n])}}}]);