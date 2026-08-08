import fs from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { SpreadsheetFile, Workbook } from "@oai/artifact-tool";

// 使用真实工作簿结构构造 Excel fixture，测试多 sheet 和同 sheet 多表区域。
const scriptDir = path.dirname(fileURLToPath(import.meta.url));
const outputPath = path.resolve(
  scriptDir,
  "../tests/fixtures/multiformat_e2e/source/budget_and_risk_register.xlsx",
);
await fs.mkdir(path.dirname(outputPath), { recursive: true });

const workbook = Workbook.create();
const headerFormat = {
  fill: "#17324D",
  font: { bold: true, color: "#FFFFFF" },
  horizontalAlignment: "center",
  verticalAlignment: "center",
  wrapText: true,
};
const bodyBorder = { preset: "all", style: "thin", color: "#D7DEE7" };

function styleRegion(sheet, rangeAddress, headerAddress) {
  sheet.getRange(rangeAddress).format.borders = bodyBorder;
  sheet.getRange(headerAddress).format = headerFormat;
  sheet.getRange(rangeAddress).format.wrapText = true;
}

const budget = workbook.worksheets.add("预算总表");
budget.showGridLines = false;
budget.getRange("A1:G7").values = [
  ["部门", "项目", "季度", "预算金额", "已使用金额", "负责人", "备注"],
  ["销售一部", "华东渠道拓展", "2026Q3", 180000, 62500, "李晨", "含展会与客户活动"],
  ["交付中心", "重点项目驻场支持", "2026Q3", 96000, 24500, "周楠", "含差旅与加班餐补"],
  ["产品部", "AI 知识库重构", "2026Q3", 135000, 48200, "王璐", "含模型调用与外包测试"],
  ["行政部", "办公室设备更新", "2026Q3", 68000, 12000, "陈希", "优先更换会议室设备"],
  ["财务部", "报销流程电子化", "2026Q3", 45000, 8000, "宋妍", "预计 8 月启动"],
  ["信息安全部", "终端检测升级", "2026Q3", 88000, 31000, "赵峰", null],
];
budget.getRange("A10:G13").values = [
  ["审批统计", "数量", "平均处理时长", "超时数量", "负责人", "复核日期", "说明"],
  ["待审批申请", 12, "1.8 天", 2, "宋妍", "2026-07-01", "需关注金额较大的申请"],
  ["已退回申请", 4, "0.7 天", 0, "宋妍", "2026-07-01", "主要缺少附件"],
  ["已完成申请", 36, "2.1 天", 1, "宋妍", "2026-07-01", null],
];
styleRegion(budget, "A1:G7", "A1:G1");
styleRegion(budget, "A10:G13", "A10:G10");
budget.getRange("D2:E7").format.numberFormat = "#,##0";
budget.getRange("A1:G13").format.rowHeight = 28;
budget.getRange("A:A").format.columnWidth = 14;
budget.getRange("B:B").format.columnWidth = 24;
budget.getRange("C:F").format.columnWidth = 15;
budget.getRange("G:G").format.columnWidth = 30;
budget.freezePanes.freezeRows(1);

const risk = workbook.worksheets.add("风险清单");
risk.showGridLines = false;
risk.getRange("A1:F7").values = [
  ["风险编号", "风险描述", "影响等级", "触发条件", "应对措施", "责任人"],
  ["R-001", "预算超支导致项目延期", "高", "连续两月消耗超预算 20%", "冻结非核心支出并重排里程碑", "李晨"],
  ["R-002", "供应商交付延迟", "中", "关键里程碑延期超过 5 天", "启动备选供应商并拆分交付范围", "周楠"],
  ["R-003", "知识库数据权限配置错误", "高", "非授权成员可访问受限条目", "按权限组复核并补审计日志", "王璐"],
  ["R-004", "上线窗口与客户变更冲突", "中", "客户冻结期提前开始", "准备回滚方案并调整发布节奏", "陈希"],
  ["R-005", "模型 API 配额不足", "中", "连续出现 429 或 TPM 超限", "启用限流、退避和降级模型", "王璐"],
  ["R-006", "审计证据缺失", "低", "审批附件未归档", "补齐归档检查并按月抽查", null],
];
styleRegion(risk, "A1:F7", "A1:F1");
risk.getRange("A1:F7").format.rowHeight = 36;
risk.getRange("A:A").format.columnWidth = 13;
risk.getRange("B:B").format.columnWidth = 28;
risk.getRange("C:C").format.columnWidth = 12;
risk.getRange("D:E").format.columnWidth = 32;
risk.getRange("F:F").format.columnWidth = 12;
risk.freezePanes.freezeRows(1);

const supplier = workbook.worksheets.add("供应商评分");
supplier.showGridLines = false;
supplier.getRange("A1:G6").values = [
  ["供应商", "交付能力", "成本", "安全合规", "服务保障", "综合得分", "结论"],
  ["云启科技", 4.5, 4.0, 4.8, 4.2, 4.4, "建议准入"],
  ["星河系统", 4.2, 4.6, 3.9, 4.0, 4.2, "需补安全材料"],
  ["同舟数据", 3.8, 4.3, 4.7, 4.5, 4.3, "可进入复审"],
  ["远帆咨询", 4.1, 3.7, 3.6, 4.4, 4.0, "限制数据访问"],
  ["北辰云服", 4.6, 3.9, 4.5, 4.6, 4.4, null],
];
styleRegion(supplier, "A1:G6", "A1:G1");
supplier.getRange("B2:F6").format.numberFormat = "0.0";
supplier.getRange("A1:G6").format.rowHeight = 30;
supplier.getRange("A:A").format.columnWidth = 18;
supplier.getRange("B:F").format.columnWidth = 14;
supplier.getRange("G:G").format.columnWidth = 22;
supplier.freezePanes.freezeRows(1);

const output = await SpreadsheetFile.exportXlsx(workbook);
await output.save(outputPath);
console.log(outputPath);
