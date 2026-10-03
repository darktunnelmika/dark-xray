"""Read-only sales reporting. A delivery/activation change is not a new payment."""
from __future__ import annotations

SALES_CTE="""WITH confirmed AS (
  SELECT o.id,o.owner,MIN(p.updated_at) paid_at
  FROM commerce_orders o JOIN commerce_payments p ON p.order_id=o.id AND p.owner=o.owner
    AND p.amount_minor=o.amount_minor AND p.currency=o.currency AND p.status='paid'
  GROUP BY o.owner,o.id
), sales AS (
  SELECT o.id,o.owner,o.buyer_telegram_id,o.product_id,o.amount_minor,o.currency,
    o.order_type,c.paid_at,'service' kind
  FROM commerce_orders o JOIN confirmed c ON c.id=o.id AND c.owner=o.owner
  WHERE o.status IN ('paid','provisioned','provisioned_waiting_activation','renewed')
  UNION ALL
  SELECT o.id,o.owner,o.buyer_telegram_id,o.plan_id,o.amount_minor,o.currency,
    o.kind,MIN(l.created_at),'representative'
  FROM representative_market_orders o JOIN customer_wallet_ledger l
    ON l.owner=o.owner AND l.telegram_id=o.buyer_telegram_id
    AND l.reference='representative:'||o.id AND l.delta_minor=-o.amount_minor
    AND l.currency=o.currency AND l.kind IN ('rep_purchase','rep_renewal')
  WHERE o.status IN ('paid','provisioned','renewed')
    AND NOT EXISTS (SELECT 1 FROM customer_wallet_ledger r WHERE r.owner=o.owner
      AND r.reference='refund:'||o.id AND r.kind='rep_refund')
  GROUP BY o.owner,o.id
) """

ORDERS_CTE="""WITH orders AS (
  SELECT id,owner,amount_minor,currency,status,created_at FROM commerce_orders
  UNION ALL
  SELECT id,owner,amount_minor,currency,status,created_at FROM representative_market_orders
) """

def sales_summary(db,owner:str,start:float,end:float)->dict:
    rows=[dict(r) for r in db.execute(SALES_CTE+"""SELECT currency,COUNT(*) count,
      COALESCE(SUM(amount_minor),0) amount FROM sales
      WHERE owner=? AND paid_at>=? AND paid_at<? GROUP BY currency ORDER BY currency""",(owner,start,end))]
    return {'count':sum(int(r['count']) for r in rows),
            'amounts':{r['currency']:int(r['amount']) for r in rows}}
