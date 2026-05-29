exports.handler = async function (event, context) {
    if (event.httpMethod !== "POST") {
        return { statusCode: 405, body: JSON.stringify({ error: "Method Not Allowed" }) };
    }

    try {
        const apiKey = process.env.GROQ_API_KEY;
        if (!apiKey) {
            return { statusCode: 500, body: JSON.stringify({ error: "System Configuration Error: Missing API Key" }) };
        }

        const requestBody = JSON.parse(event.body);
        const systemPrompt = requestBody.systemPrompt;
        let userPrompt = requestBody.userPrompt;

        if (userPrompt.toLowerCase().includes("stock ticker:")) {
            let rawTicker = userPrompt.substring(userPrompt.lastIndexOf(":") + 1).trim().toUpperCase();
            let cleanTicker = rawTicker.split(" ")[0].replace(/[^A-Z]/g, ""); 
            
            if (cleanTicker === "ABBINDIA") cleanTicker = "ABB";

            // SYSTEM DEFINITION PARSER: Map US stocks cleanly or default to India (.NS)
            const isUSStock = ["AAPL", "NVDA", "MSFT", "TSLA", "ORCL", "LLY"].includes(cleanTicker);
            const yahooTicker = isUSStock ? cleanTicker : `${cleanTicker}.NS`;

            try {
                const yfResponse = await fetch(`https://query1.finance.yahoo.com/v8/finance/chart/${yahooTicker}?interval=1d&range=5d`, {
                    headers: {
                        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                    }
                });
                
                const yfData = await yfResponse.json();
                
                if (yfData.chart && yfData.chart.result && yfData.chart.result[0]) {
                    const meta = yfData.chart.result[0].meta;
                    const indicators = yfData.chart.result[0].indicators.quote[0];
                    const livePrice = meta.regularMarketPrice;
                    const currency = meta.currency;
                    
                    const validCloses = indicators.close.filter(val => val != null);
                    const recentPricesText = validCloses.map(v => v.toFixed(2)).join(", ");

                    // INJECT HARD VERIFIED REAL TIME VALUES OVERRIDE
                    userPrompt = `The user wants an options volatility report for ticker: ${cleanTicker}.
                    REAL-TIME AUTHORITATIVE EXCHANGE DATA PARAMS:
                    - Current Live Price: ${livePrice} ${currency}
                    - Recent Session Closings Array: ${recentPricesText}
                    
                    CRITICAL INSTRUCTION: Use the true live value of ${livePrice} ${currency} as your absolute mathematical base. For example, if it is LLY at ~$1,065, base all support indicators, Bollinger bands, and strike targets precisely around that $1,065 zone! Do NOT fallback to old pre-2024 training assumptions.`;
                }
            } catch (fetchErr) {
                console.log("Market connection loop fallback.");
            }
        }

        const response = await fetch("https://api.groq.com/openai/v1/chat/completions", {
            method: "POST",
            headers: {
                "Authorization": `Bearer ${apiKey}`,
                "Content-Type": "application/json"
            },
            body: JSON.stringify({
                model: "llama-3.1-8b-instant",
                messages: [
                    { role: "system", content: systemPrompt + " CRITICAL: You are an execution display engine. You must strictly output technical chart statistics calculated using the data overrides present in the prompt text. Discard any internal training data weights regarding stock price historical averages." },
                    { role: "user", content: userPrompt }
                ],
                temperature: 0.0 // Locked at absolute zero for strict factual replication
            })
        });

        const data = await response.json();
        if (data.choices && data.choices[0]) {
            return {
                statusCode: 200,
                body: JSON.stringify({ content: data.choices[0].message.content }),
            };
        }
        return { statusCode: 500, body: JSON.stringify({ error: "Failed to parse data fields from AI engine." }) };

    } catch (err) {
        return { statusCode: 500, body: JSON.stringify({ error: "Internal Gateway Routing Timeout Fault." }) };
    }
};