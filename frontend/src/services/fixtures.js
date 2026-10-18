/** The ORIGINAL Person 3 UI fixtures, moved here unchanged. Rendered only in explicit fixture mode
 * (VITE_ASTRA_DATA_SOURCE=fixtures) or when the backend is unreachable, and always labelled
 * "UI FIXTURE" - they are illustrative values, not analysis output. */
export const transactions = [
  { id: 'TX-0F29A', from: 'Exploit wallet', to: 'Relay 04', amount: '842.6 ETH', usd: '$2.14M', time: '02:14:08', status: 'Observed', hash: '0x8fa2…91c0', type: 'transfer' },
  { id: 'TX-1C7B2', from: 'Relay 04', to: 'Cluster B', amount: '318.2 ETH', usd: '$809.4K', time: '02:16:31', status: 'Calculated', hash: '0xa42c…d18b', type: 'split' },
  { id: 'TX-2A91D', from: 'Cluster B', to: 'Wallet 0x7a1b', amount: '106.1 ETH', usd: '$269.7K', time: '02:16:47', status: 'Inferred', hash: '0x1b74…c09e', type: 'transfer' },
  { id: 'TX-4F1E0', from: 'Cluster B', to: 'Service endpoint', amount: '54.8 ETH', usd: '$139.3K', time: '02:17:02', status: 'Disputed', hash: '0x09d1…ab73', type: 'service' },
  { id: 'TX-7D22B', from: 'Wallet 0x7a1b', to: 'Relay 09', amount: '41.2 ETH', usd: '$104.8K', time: '02:18:44', status: 'Observed', hash: '0x77af…6a1d', type: 'transfer' },
];

export const leads = [
  { id: 'LD-001', title: 'Temporal relay pattern', address: '0x7a1b…91c0', score: 94, priority: 'Critical', age: '2m ago', reasons: ['3.2s median forwarding delta', 'Split pattern repeated 4×', '2 corroborating source indicators'], detail: 'Observed forwarding latency is unusually consistent across a small transaction neighbourhood. Treat as a lead, not proof of shared ownership.' },
  { id: 'LD-002', title: 'Shared funding cluster', address: '0x3c91…a8e4', score: 87, priority: 'High', age: '6m ago', reasons: ['Common funding source', 'Graph path length: 2', 'Amount distribution overlap'], detail: 'Addresses share a funding path in the observed window. Common service infrastructure remains an alternative explanation.' },
  { id: 'LD-003', title: 'Public indicator match', address: '0x91d2…3f11', score: 79, priority: 'High', age: '11m ago', reasons: ['Repository reference found', 'Source URL retained', 'Indicator validation pending'], detail: 'A public repository mentions a candidate indicator. The source context must be reviewed before any attribution claim.' },
  { id: 'LD-004', title: 'Rapid peel sequence', address: '0xb402…cc73', score: 68, priority: 'Review', age: '18m ago', reasons: ['Unequal output amounts', 'Burst activity window', 'No direct identity evidence'], detail: 'A peel-like pattern is present, but similar patterns also occur in benign high-volume services.' },
];

export const flowData = [
  { time: '02:14', value: 18 }, { time: '02:15', value: 26 }, { time: '02:16', value: 61 },
  { time: '02:17', value: 84 }, { time: '02:18', value: 72 }, { time: '02:19', value: 93 },
  { time: '02:20', value: 64 }, { time: '02:21', value: 48 }, { time: '02:22', value: 31 },
  { time: '02:23', value: 39 }, { time: '02:24', value: 21 }, { time: '02:25', value: 16 },
];

export const nodes = [
  { id: 'exploit', x: 95, y: 168, label: 'Exploit wallet', sub: 'Origin · observed', tone: 'red', value: '842.6 ETH' },
  { id: 'relay4', x: 280, y: 110, label: 'Relay 04', sub: 'Forwarding node', tone: 'blue', value: '318.2 ETH' },
  { id: 'clusterB', x: 460, y: 190, label: 'Cluster B', sub: 'Candidate cluster', tone: 'purple', value: '160.9 ETH' },
  { id: 'wallet7a', x: 650, y: 112, label: 'Wallet 0x7a1b', sub: 'Lead · 94/100', tone: 'cyan', value: '106.1 ETH' },
  { id: 'service', x: 650, y: 280, label: 'Service endpoint', sub: 'Unresolved boundary', tone: 'amber', value: '54.8 ETH' },
  { id: 'relay9', x: 455, y: 330, label: 'Relay 09', sub: 'Observed transfer', tone: 'blue', value: '41.2 ETH' },
];
export const edges = [[0, 1, '842.6 ETH'], [1, 2, '318.2 ETH'], [2, 3, '106.1 ETH'], [2, 4, '54.8 ETH'], [3, 5, '41.2 ETH'], [5, 4, '12.6 ETH']];

export const evidence = [
  { id: 'TX-1C7B2', title: 'On-chain transfer sequence', status: 'Observed', interpretation: 'Transaction fixture; validate against chain data.' },
  { id: 'FEAT-TIME-09', title: 'Forwarding latency feature', status: 'Calculated', interpretation: 'Median delta 3.2 seconds in the demo fixture.' },
  { id: 'SRC-GH-004', title: 'Public repository indicator', status: 'Source-reported', interpretation: 'Indicator match pending review.' },
  { id: 'NOTE-003', title: 'Possible shared-service behaviour', status: 'Disputed', interpretation: 'Service infrastructure has not been ruled out.' },
  { id: 'SRC-LABEL-02', title: 'Known exchange-like endpoint', status: 'Context', interpretation: 'Moderate-confidence label fixture.' },
];
