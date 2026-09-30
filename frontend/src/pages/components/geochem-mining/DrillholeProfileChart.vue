<template>
  <div class="profile-shell">
    <svg ref="svgElement" v-if="points.length && displayPoints.length" :viewBox="`0 0 ${width} ${height}`" role="img" :aria-label="`${element}钻孔纵向变化图`">
      <rect :x="plotLeft" :y="plotTop" :width="plotWidth" :height="plotHeight" fill="#fbfcfe" stroke="#dce4ef" />
      <g v-for="band in geologyBands" :key="band.key">
        <rect :x="plotLeft" :y="y(band.start)" :width="plotWidth" :height="Math.max(2, y(band.end)-y(band.start))" :fill="band.color" opacity="0.16" />
        <text :x="plotLeft + plotWidth + 8" :y="(y(band.start)+y(band.end))/2" dominant-baseline="middle" class="geology-label">{{ band.label }}</text>
      </g>
      <g v-for="tick in depthTicks" :key="tick">
        <line :x1="plotLeft" :x2="plotLeft+plotWidth" :y1="y(tick)" :y2="y(tick)" stroke="#e7edf4" />
        <text :x="plotLeft-8" :y="y(tick)+4" text-anchor="end" class="axis-label">{{ tick.toFixed(0) }}</text>
      </g>
      <line v-for="reference in references" :key="reference.label" :x1="x(reference.value)" :x2="x(reference.value)" :y1="plotTop" :y2="plotTop+plotHeight" :stroke="reference.color" :stroke-dasharray="reference.dash" />
      <polyline v-if="showNormal" :points="polyline" fill="none" stroke="#1769aa" stroke-width="2.2" />
      <g v-for="point in displayPoints" :key="point.assay_id">
        <circle class="profile-point" :cx="x(point.value)" :cy="y(point.mid_depth)" r="4" :fill="levelColor(point.mining_level || point.anomaly_level)" stroke="#fff" stroke-width="1.2" @click="emit('pointClick', point)">
          <title>{{ tooltip(point) }}</title>
        </circle>
      </g>
      <text :x="plotLeft+plotWidth/2" :y="height-12" text-anchor="middle" class="axis-title">元素含量（log1p 对数显示，悬停查看原值）</text>
      <text transform="rotate(-90)" :x="-(plotTop+plotHeight/2)" y="20" text-anchor="middle" class="axis-title">钻孔深度（m，向下增加）</text>
      <g :transform="`translate(${plotLeft},18)`" class="legend">
        <g v-for="(reference,index) in references" :key="reference.label" :transform="`translate(${index*150},0)`">
          <text x="0" y="0">{{ reference.label }}</text><line x1="72" x2="102" y1="-4" y2="-4" :stroke="reference.color" :stroke-dasharray="reference.dash" />
        </g>
      </g>
    </svg>
    <el-empty v-else :description="points.length ? '当前钻孔与元素没有异常区间，可关闭“仅看异常段”查看完整曲线' : '请选择元素和钻孔查看纵向变化'" />
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from "vue";

const props = withDefaults(defineProps<{ points: any[]; element: string; showNormal?: boolean }>(), { showNormal: true });
const emit = defineEmits<{ pointClick: [point:any] }>();
const svgElement = ref<SVGSVGElement | null>(null);
const width = 980, height = 560, plotLeft = 88, plotTop = 38, plotWidth = 684, plotHeight = 470;
const numeric = (value:any) => Number.isFinite(Number(value)) ? Number(value) : 0;
const minDepth = computed(() => Math.min(...props.points.map(item => numeric(item.from_depth)), 0));
const maxDepth = computed(() => Math.max(...props.points.map(item => numeric(item.to_depth)), 1));
const maxValue = computed(() => Math.max(...props.points.flatMap(item => [numeric(item.value), numeric(item.threshold_T_auto), numeric(item.boundary_grade_ppm), numeric(item.industrial_grade_ppm)]), 1));
const y = (depth:number) => plotTop + (numeric(depth)-minDepth.value) / Math.max(1, maxDepth.value-minDepth.value) * plotHeight;
const x = (value:number) => plotLeft + Math.log1p(Math.max(0,numeric(value))) / Math.max(1e-9,Math.log1p(maxValue.value)) * plotWidth;
const displayPoints = computed(() => props.showNormal ? props.points : props.points.filter(item => {
  const level=String(item.mining_level || item.anomaly_level || "");
  return level && !["背景范围","非异常","无可靠估计"].includes(level);
}));
const polyline = computed(() => displayPoints.value.map(item => `${x(item.value)},${y(item.mid_depth)}`).join(" "));
const depthTicks = computed(() => Array.from({length:6},(_,i)=>minDepth.value+(maxDepth.value-minDepth.value)*i/5));
const first = computed(() => props.points[0] || {});
const references = computed(() => [
  {label:"背景值",value:numeric(first.value.background_mean),color:"#526579",dash:"4 3"},
  {label:"统计异常下限",value:numeric(first.value.threshold_T_auto),color:"#FD8D3C",dash:"4 3"},
  {label:"边界品位",value:numeric(first.value.boundary_grade_ppm),color:"#A50F15",dash:"5 3"},
  {label:"最低工业品位",value:numeric(first.value.industrial_grade_ppm),color:"#7A0177",dash:"6 3"},
].filter(item=>item.value>0));
const geologyBands = computed(() => {
  const bands:any[]=[];
  for(const item of props.points){
    const label=[item.section_geobody_key,item.lithology,item.weathering].filter(Boolean).join(" · ") || "未匹配分段";
    const previous=bands[bands.length-1];
    if(previous && previous.label===label && Math.abs(previous.end-numeric(item.from_depth))<1e-6) previous.end=numeric(item.to_depth);
    else bands.push({key:`${item.assay_id}-${label}`,label,start:numeric(item.from_depth),end:numeric(item.to_depth),color:colorFrom(label)});
  }
  return bands;
});
function colorFrom(text:string){let hash=0;for(const char of text)hash=(hash*31+char.charCodeAt(0))>>>0;return `hsl(${hash%300} 52% 55%)`;}
function levelColor(level:string){
  const colors:any={
    "无可靠估计":"#B8C0CC",
    "背景范围":"#FFF4D6",
    "轻微相对富集":"#FEC44F",
    "相对富集":"#FEC44F",
    "强相对富集":"#FEC44F",
    "统计异常外带":"#FD8D3C",
    "统计异常中带":"#F03B20",
    "统计异常内带":"#BD0026",
    "最低边界品位":"#A50F15",
    "边界品位":"#A50F15",
    "工业品位":"#7A0177",
    "最低工业品位":"#7A0177",
  };
  if(colors[level])return colors[level];if(level?.includes("内带"))return "#c6413b";if(level?.includes("中带"))return "#e4842d";if(level?.includes("外带"))return "#d7a518";return "#1769aa";
}
function displayLevel(level:any){const text=String(level ?? "");return ({"最低边界品位":"边界品位","工业品位":"最低工业品位"} as Record<string,string>)[text] || text;}
function finiteNumber(value:any){if(value==null||value===""||typeof value==="boolean")return null;const number=Number(value);return Number.isFinite(number)?number:null;}
function displayNumber(value:any){const number=finiteNumber(value);if(number==null)return "—";return number>=10?number.toFixed(1):number.toFixed(2).replace(/0+$/,"").replace(/\.$/,"");}
function tooltip(item:any){
  const value=finiteNumber(item.value),background=finiteNumber(item.background_mean),ratio=finiteNumber(item.value_to_background_ratio);
  const calculation=value!=null&&background!=null&&background>0&&ratio!=null
    ? `含量 ${displayNumber(value)} ppm ÷ 背景值 ${displayNumber(background)} ppm = ${displayNumber(ratio)} 倍`
    : "背景值缺失，不能计算背景倍数";
  return `${props.element} | ${item.from_depth}-${item.to_depth} m | ${calculation} | ${displayLevel(item.mining_level || item.anomaly_level)} | ${item.section_geobody_key || "未匹配"} ${item.lithology || ""}`;
}
function serializedSvg(){
  if(!svgElement.value)return "";
  const copy=svgElement.value.cloneNode(true) as SVGSVGElement;
  copy.setAttribute("xmlns","http://www.w3.org/2000/svg");copy.setAttribute("width",String(width));copy.setAttribute("height",String(height));
  return new XMLSerializer().serializeToString(copy);
}
function triggerDownload(url:string,extension:string){const a=document.createElement("a");a.href=url;a.download=`${props.element || '元素'}-钻孔纵向变化.${extension}`;a.click();}
function exportSvg(){const source=serializedSvg();if(!source)return;const url=URL.createObjectURL(new Blob([source],{type:"image/svg+xml;charset=utf-8"}));triggerDownload(url,"svg");URL.revokeObjectURL(url);}
function exportPng(){const source=serializedSvg();if(!source)return;const url=URL.createObjectURL(new Blob([source],{type:"image/svg+xml;charset=utf-8"}));const image=new Image();image.onload=()=>{const canvas=document.createElement("canvas");canvas.width=width*2;canvas.height=height*2;const context=canvas.getContext("2d");if(context){context.fillStyle="#fff";context.fillRect(0,0,canvas.width,canvas.height);context.scale(2,2);context.drawImage(image,0,0,width,height);triggerDownload(canvas.toDataURL("image/png"),"png");}URL.revokeObjectURL(url);};image.src=url;}
defineExpose({exportSvg,exportPng});
</script>

<style scoped>
.profile-shell{border:1px solid #dfe7f0;background:#fff;min-height:440px;overflow:auto}.profile-shell svg{display:block;width:100%;min-width:760px;height:auto}.axis-label,.geology-label,.legend{font-size:11px;fill:#5d6d82}.geology-label{font-size:10px}.axis-title{font-size:12px;fill:#31445e;font-weight:600}.profile-point{cursor:pointer}.profile-point:hover{stroke:#143f67;stroke-width:2}
</style>
