"""Provisional price spread only; never a confirmed income/cost/margin write."""
from decimal import Decimal, localcontext, ROUND_HALF_UP
import re

RULE_VERSION = 'trial-margin/1.3'


def amount(value):
    if not isinstance(value, str) or not re.fullmatch(r'\d{1,24}(?:\.\d{1,12})?', value):
        raise ValueError('Invalid source amount')
    return Decimal(value)


def trial_for_row(row, recognition):
    result = {'status': 'unavailable', 'amountDecimal': None, 'ratePercentDecimal': None, 'currency': None,
              'pricingUnitVerified': False, 'storeScope': None, 'ruleVersion': RULE_VERSION,
              'basis': 'attachment_activity_price_minus_agent_reference_quote'}
    try:
        metadata = recognition['supplyPriceSource']
        binding = recognition['binding']
        keys = ('activityId', 'versionId', 'inputDigest', 'catalogExportSha256')
        if metadata['status'] != 'ready' or any(metadata['binding'][k] != binding[k] for k in keys):
            raise ValueError('Quote binding mismatch')
        price = row['activityPriceCandidate']
        evidence = row['agentSupplyPrices']
        quote = evidence['referenceQuote']
        if (price['status'] not in ('needs_review', 'ready') or
                evidence['status'] != 'reference_only' or not row['uniqueUpcHit'] or len(row['candidates']) != 1):
            raise ValueError('Unsupported source state')
        if any(source[k] != row[k] for source in (price, evidence) for k in ('candidateId', 'rowId')):
            raise ValueError('Row binding mismatch')
        candidate = row['candidates'][0]
        if (quote['internalCode'] != candidate['internalCode'] or quote['upc'] != row['rawUpc'] or
                metadata['source']['sourceSha256'] != recognition['catalog']['sourceSha256']):
            raise ValueError('Quote identity mismatch')
        # Trial spread uses the displayed amounts directly, even if their units are unverified.
        activity_amount, quote_amount = amount(price['amountDecimal']), amount(quote['amountDecimal'])
        originals = {item['materialId']: item['sha256'] for item in binding['materials']}
        sources = []
        for observation in price['observations']:
            if originals.get(observation['materialId']) != observation['sourceSha256']:
                raise ValueError('Attachment source mismatch')
            for cell in observation['priceCells']:
                if cell.get('issue') is not None or amount(cell['amountDecimal']) != activity_amount:
                    raise ValueError('Activity price source conflict')
                sources.append({'materialId': observation['materialId'], 'sourceSha256': observation['sourceSha256'],
                                'locator': cell['source']['locator'], 'rawText': cell['source']['rawText']})
        if not sources:
            raise ValueError('Missing activity price evidence')
        with localcontext() as context:
            context.prec = 80
            value = activity_amount - quote_amount
            rate = format((value / activity_amount * 100).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP), 'f') if activity_amount > 0 else None
            output = format(value, 'f')
            if '.' in output: output = output.rstrip('0').rstrip('.')
        currency = price.get('currency') if price.get('currency') == quote.get('currency') else None
        result.update(status='ready', amountDecimal=output, ratePercentDecimal=rate, rateRounding='HALF_UP_2', currency=currency, activityPriceDecimal=price['amountDecimal'],
                      referenceQuoteDecimal=quote['amountDecimal'], internalCode=candidate['internalCode'],
                      sourceBinding={k: binding[k] for k in keys},
                      activityPriceSources=sources,
                      referenceQuoteSource=metadata['source'] | {'sourceRowNumber': quote['sourceRowNumber'],
                          'rawAmount': quote['rawAmount'], 'spec': quote['spec']},
                      reasonCode=None)
    except (ValueError, KeyError, TypeError, AttributeError):
        result['reasonCode'] = 'TRIAL_MARGIN_SOURCE_UNAVAILABLE'
    return result


def enrich_trial_margin(recognition):
    if recognition.get('status') != 'ready':
        return recognition
    rows = [row | {'operatingMetrics': {**row.get('operatingMetrics', {}),
                                       'trialMargin': trial_for_row(row, recognition)}}
            for row in recognition['rows']]
    return recognition | {'rows': rows}
